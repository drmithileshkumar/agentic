#!/usr/bin/env python3
"""Regression tests for domain/tier classification. Stdlib only, no pytest.

    python problems/test_classify.py

Every case here is a problem that was misclassified at some point and got a
rule written for it. They run against the live manifest, so they also catch a
manifest that drifted away from what the classifier would produce today.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_candidates import (  # noqa: E402
    DOMAINS,
    MINIF2F_DOMAIN_HINTS,
    TIERS,
    UNKNOWN_DOMAIN,
    classify,
    guess_tier,
    statement_complexity,
)

MANIFEST = Path(__file__).resolve().parent / "candidates.jsonl"

# (source id or declaration name, acceptable domains, what this case guards)
DOMAIN_CASES: list[tuple[str, tuple[str, ...], str]] = [
    ("Munkres|exercise_13_8a", ("TOP",),
     "`Basis` must not match inside `IsTopologicalBasis`"),
    ("Munkres|exercise_13_8b", ("TOP",), "same"),
    ("Munkres|exercise_16_6", ("TOP",), "same"),
    ("Munkres|exercise_29_1", ("TOP",),
     "no keyword hits; the textbook prior has to carry it"),
    ("Rudin|exercise_1_17", ("RAN",),
     "a norm identity in R^k is analysis, not geometry"),
    ("Rudin|exercise_1_18a", ("RAN",), "same"),
    ("Rudin|exercise_1_2", ("NUM", "RAN"),
     "no rational squares to 12 — irrationality or the real number system, "
     "both defensible; Rudin's prior picks RAN"),
    ("imo_1964_p1_2", ("NUM",),
     "divisibility; must not be forced to ALG by the `imo_` prefix"),
    ("imo_1984_p2", ("NUM",), "same"),
    ("Dummit-Foote|exercise_1_1_2a", ("ABA",),
     "a bare non-commutativity claim, carried by the textbook prior"),
    ("Ireland-Rosen|exercise_3_5", ("NUM",), "carried by the textbook prior"),
    ("Herstein|exercise_4_1_19", ("ABA",),
     "quaternions: the prior must beat a stray point for the R in the statement"),
    ("Ireland-Rosen|exercise_2_21", ("NUM",),
     "`divisors` must match inside `Nat.divisors`"),
    ("mathd_algebra_149", ("ALG",), "miniF2F topic hint still applies"),
    ("mathd_numbertheory_780", ("NUM",), "miniF2F topic hint still applies"),
]

# (source id or declaration name, acceptable tiers, what this case guards)
TIER_CASES: list[tuple[str, tuple[str, ...], str]] = [
    ("imo_1964_p1_2", ("H",), "a short IMO statement is still an IMO problem"),
    ("Putnam|exercise_2001_a5", ("H",), "Putnam floor"),
    ("amc12a_2009_p2", ("M", "H"), "AMC never lands in Easy"),
]


def load_manifest() -> dict[str, dict]:
    if not MANIFEST.exists():
        print(f"error: {MANIFEST} not found; run extract_candidates.py first",
              file=sys.stderr)
        raise SystemExit(1)
    index: dict[str, dict] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for key in (record.get("source_id"), record.get("name")):
            if key:
                index.setdefault(key, record)
    return index


def classify_record(record: dict) -> tuple[str, str, float]:
    label = record.get("source_dataset") or ""
    return classify(
        statement=record.get("statement") or "",
        proof=record.get("proof") or "",
        informal=record.get("informal") or "",
        source_id=record.get("source_id") or record.get("source_file") or "",
        source_label=label,
        topic_hints=MINIF2F_DOMAIN_HINTS if label == "miniF2F" else (),
    )


def main() -> int:
    index = load_manifest()
    failures: list[str] = []
    checks = 0

    for key, expected, why in DOMAIN_CASES:
        record = index.get(key)
        if record is None:
            failures.append(f"{key}: not in the manifest")
            continue
        domain, _tier, score = classify_record(record)
        checks += 1
        if domain not in expected:
            failures.append(
                f"{key}: domain {domain} (score {score}), expected one of "
                f"{'/'.join(expected)} — {why}")

    for key, expected, why in TIER_CASES:
        record = index.get(key)
        if record is None:
            failures.append(f"{key}: not in the manifest")
            continue
        _domain, tier, _score = classify_record(record)
        checks += 1
        if tier not in expected:
            failures.append(
                f"{key}: tier {tier}, expected one of {'/'.join(expected)} — {why}")

    # Properties that must hold across the whole manifest, not just samples.
    manifest = list({id(r): r for r in index.values()}.values())
    unknown = [r for r in manifest if r.get("domain") == UNKNOWN_DOMAIN]
    if len(unknown) > 5:
        failures.append(f"{len(unknown)} entries in {UNKNOWN_DOMAIN}; "
                        f"the fallbacks are supposed to keep this near zero")
    checks += 1

    bad = [r for r in manifest
           if r.get("domain") not in DOMAINS + [UNKNOWN_DOMAIN]
           or r.get("tier") not in TIERS]
    if bad:
        failures.append(f"{len(bad)} entries carry a domain or tier outside the schema")
    checks += 1

    # Every tier must be reachable, or the rubric has collapsed to a constant.
    tiers_seen = {r.get("tier") for r in manifest}
    if set(TIERS) - tiers_seen:
        failures.append(f"tiers {sorted(set(TIERS) - tiers_seen)} are unreachable")
    checks += 1

    # The rubric has to be monotone in the obvious direction.
    simple = statement_complexity("theorem t : 1 + 1 = 2")
    involved = statement_complexity(
        "theorem t {G : Type*} [Group G] (a b c : G) (h1 : a * b = c) "
        "(h2 : b * c = a) (h3 : c * a = b) : ∀ x : G, ∃ y : G, x * y = y * x")
    if not simple < involved:
        failures.append(f"complexity not monotone: {simple} !< {involved}")
    checks += 1

    if guess_tier("theorem t : 1 + 1 = 2", "", "miniF2F imo_1900_p1") != "H":
        failures.append("competition floor did not override a trivial statement")
    checks += 1

    for line in failures:
        print(f"FAIL  {line}")
    print(f"\n{checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
