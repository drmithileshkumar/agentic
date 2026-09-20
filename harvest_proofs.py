#!/usr/bin/env python3
"""Harvest proofs the agent found out of the run logs and into the problem set.

A proof that only exists as a JSONL tactic trace is invisible to Lean: nothing
compiles it, `leanblueprint checkdecls` cannot see it, and the blueprint keeps
showing the theorem as unproved. This script closes that gap by writing the
winning tactic sequence into the manifest record's `proof` field and handing the
rest to `problems/manifest.py promote --verify`, which already knows how to lay
a problem out on disk and flip the verified flag.

    python harvest_proofs.py --dry-run     # show what would happen
    python harvest_proofs.py               # do it

Run it with the harness venv:
    C:\\lean\\harness-venv\\Scripts\\python.exe harvest_proofs.py --dry-run

Idempotent: entries already marked `verified: true` are skipped, so running it
twice changes nothing the second time.

IMPORTANT -- the winning proof is NOT `tactics_used`
----------------------------------------------------
The `done` event's `tactics_used` lists every tactic the agent proposed,
failures included. For SET_E_01 that is::

    ["rfl",                                   <- error
     "simp [Set.mem_inter_iff, and_comm]",    <- error
     "ext x",                                 <- advanced
     "simp [Set.mem_inter_iff, and_comm]"]    <- proved

Joining those four produces a proof that does not compile. A tactic that errored
left the proof state untouched, so the real proof is only the steps whose
outcome was `advanced` or `proved`, in order -- here `ext x` then the `simp`.
This script reads the step events for that reason and ignores `tactics_used`.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from argparse import Namespace
from pathlib import Path

LOG_ROOT = Path(r"C:\lean\logs")
PROBLEMS_DIR = Path(r"C:\lean\MyMathlibProject\problems")
MANIFEST = PROBLEMS_DIR / "candidates.jsonl"
INDEX_MODULE = PROBLEMS_DIR / "Problems.lean"

# Outcomes that actually moved the proof forward. Everything else left the
# state untouched and must not appear in the reconstructed proof.
PROGRESS_OUTCOMES = ("advanced", "proved")

# Synthetic smoke-test theorem; it is not in the benchmark.
SKIP_NAMES = {"toy_comm"}

THEOREM_NAME_RE = re.compile(r"\btheorem\s+(\w+)")


# ---------------------------------------------------------------------------
# Reading the logs
# ---------------------------------------------------------------------------

def read_events(path: Path) -> list[dict]:
    events = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return events
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def harvest_one(path: Path):
    """Return (name, tactics, run) for a PROVED proof file, else None."""
    events = read_events(path)
    done = next((e for e in events if e.get("event") == "done"), None)
    if not done or done.get("result") != "PROVED":
        return None

    match = THEOREM_NAME_RE.search(done.get("theorem", ""))
    if not match:
        return None
    name = match.group(1)

    steps = sorted((e for e in events if e.get("event") == "step"),
                   key=lambda e: e.get("step") or 0)
    tactics = []
    for step in steps:
        if step.get("outcome") not in PROGRESS_OUTCOMES:
            continue
        tactic = step.get("tactic")
        if isinstance(tactic, str) and tactic.strip():
            tactics.append(" ".join(tactic.split()))

    if not tactics:
        return None
    return name, tactics, path.parent.name


def collect(log_root: Path) -> dict:
    """Best proof per theorem name: fewest tactics wins."""
    best: dict[str, dict] = {}
    for path in sorted(log_root.glob("*/proof_*.jsonl")):
        got = harvest_one(path)
        if not got:
            continue
        name, tactics, run = got
        entry = {"name": name, "tactics": tactics, "run": run,
                 "file": path.name, "variants": 1}
        prior = best.get(name)
        if prior is None:
            best[name] = entry
        else:
            prior["variants"] += 1
            if len(tactics) < len(prior["tactics"]):
                entry["variants"] = prior["variants"]
                best[name] = entry
    return best


def proof_text(tactics: list[str]) -> str:
    return "by\n  " + "\n  ".join(tactics)


# ---------------------------------------------------------------------------
# Promotion via manifest.py
# ---------------------------------------------------------------------------

def load_manifest_module():
    sys.path.insert(0, str(PROBLEMS_DIR))
    import manifest  # noqa: E402
    return manifest


def module_name(problem_path: str) -> str:
    """problems/SET/E/SET_E_01.lean -> SET.E.SET_E_01, matching Problems.lean."""
    rel = problem_path[len("problems/"):] if problem_path.startswith("problems/") else problem_path
    return rel[:-len(".lean")].replace("/", ".") if rel.endswith(".lean") else rel


def already_imported(module: str) -> bool:
    if not INDEX_MODULE.exists():
        return False
    text = INDEX_MODULE.read_text(encoding="utf-8")
    return bool(re.search(r"^import\s+" + re.escape(module) + r"\s*$", text, re.M))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Harvest proved tactic sequences from run logs into the problem set.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen; write nothing")
    parser.add_argument("--log-root", default=str(LOG_ROOT))
    args = parser.parse_args(argv)

    log_root = Path(args.log_root)
    if not log_root.is_dir():
        sys.exit("log root not found: %s" % log_root)

    manifest = load_manifest_module()
    records = manifest.load(MANIFEST)
    by_name = {r.get("name"): r for r in records if r.get("name")}

    found = collect(log_root)
    print("=" * 74)
    print("HARVEST%s" % ("  (dry run -- nothing will be written)" if args.dry_run else ""))
    print("=" * 74)
    print("  log root        : %s" % log_root)
    print("  manifest        : %s" % MANIFEST)
    print("  proved theorems : %d distinct" % len(found))
    print()

    promoted, skipped, failed = [], [], []

    for name in sorted(found):
        entry = found[name]
        tactics = entry["tactics"]
        proof = proof_text(tactics)

        if name in SKIP_NAMES:
            skipped.append((name, "smoke-test theorem, not in the dataset"))
            continue
        record = by_name.get(name)
        if record is None:
            skipped.append((name, "no manifest entry"))
            continue
        if record.get("verified"):
            skipped.append((name, "already verified: true"))
            continue
        if any("sorry" in t for t in tactics):
            failed.append((name, "winning tactics contain `sorry`"))
            continue

        domain = record.get("domain", "")
        tier = record.get("tier", "")
        if domain not in manifest.DOMAINS or tier not in manifest.TIERS:
            failed.append((name, "bad domain/tier in manifest: %r/%r" % (domain, tier)))
            continue

        print("-" * 74)
        print("  %s   (%s/%s, id %s)" % (name, domain, tier, record.get("id")))
        print("  from %s/%s%s" % (entry["run"], entry["file"],
                                  "  [%d runs proved it; shortest kept]" % entry["variants"]
                                  if entry["variants"] > 1 else ""))
        for tactic in tactics:
            print("      %s" % tactic)

        # cmd_promote reads the proof off the record, so set it first; the same
        # dict object is the one find_one() will hand back.
        record["proof"] = proof

        promote_args = Namespace(
            manifest=str(MANIFEST), id=record["id"],
            domain=domain, tier=tier, index=None,
            verify=True, force=True, dry_run=args.dry_run,
        )
        try:
            rc = manifest.cmd_promote(promote_args, records)
        except SystemExit as exc:
            rc = exc.code or 1
        except Exception as exc:                      # noqa: BLE001
            failed.append((name, "%s: %s" % (type(exc).__name__, exc)))
            continue

        if rc != 0:
            failed.append((name, "manifest.py promote returned %s" % rc))
            continue
        promoted.append((name, record))

    # ---- summary -------------------------------------------------------
    print()
    print("=" * 74)
    print("SUMMARY")
    print("=" * 74)
    print("  promoted : %d" % len(promoted))
    for name, record in promoted:
        print("     %-18s -> %s" % (name, record.get("problem_path") or "(dry run)"))
    print("  skipped  : %d" % len(skipped))
    for name, why in skipped:
        print("     %-18s %s" % (name, why))
    print("  failed   : %d" % len(failed))
    for name, why in failed:
        print("     %-18s %s" % (name, why))

    # ---- manual follow-up ---------------------------------------------
    if promoted:
        print()
        print("=" * 74)
        print("NEXT STEP -- add these imports by hand")
        print("=" * 74)
        print("  file: %s" % INDEX_MODULE)
        print()
        print("  `promote` writes the .lean file but does not touch the index, and")
        print("  Problems.lean is what `lake build` follows. Until these lines exist")
        print("  the problems are verified in the manifest but compiled by nothing,")
        print("  which `python problems/manifest.py check` will report as an error.")
        print()
        for name, record in promoted:
            path = record.get("problem_path")
            if not path:
                print("     (dry run -- path assigned on the real run)  %s" % name)
                continue
            module = module_name(path)
            mark = "  # already present" if already_imported(module) else ""
            print("     import %s%s" % (module, mark))
        print()
        print("  then:")
        print("     cd C:\\lean\\MyMathlibProject && lake build")
        print("     python problems/manifest.py check")
        print("     python problems/gen_blueprint.py")

    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
