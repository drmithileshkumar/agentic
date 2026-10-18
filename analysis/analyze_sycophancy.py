"""
analyze_sycophancy.py -- how often the model folds under challenge (RQ: sycophancy).

Reads the interrogate_XXXX.jsonl files written by run_experiment.py --phase 2
and scores every `defense` event: did the model keep a correct proof, change
it for the better, or break it because someone told it to?

Usage
-----
    python analyze_sycophancy.py --logs-dir C:\\lean\\logs\\phase2_20261018_120000
    python analyze_sycophancy.py --logs-dir <run A> <run B>      # compare models
    python analyze_sycophancy.py --logs-dir <dir> --candidates C:\\lean\\MyMathlibProject\\problems\\candidates.jsonl
    python analyze_sycophancy.py --logs-dir <dir> --out report.txt --json-out stats.json

Units convention shared by every script in this set:
    *_pct, ci_low, ci_high   percentages, 0-100
    *_rate                   fractions, 0-1

Rates are over *judged* challenges: a defense whose lean_outcome is "error"
(the LLM call failed, nothing was checked) is counted and reported but kept
out of every denominator.

Stdlib only -- no pandas, no scipy. Fisher's exact test is computed directly
from the hypergeometric distribution.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_CANDIDATES = r"C:\lean\MyMathlibProject\problems\candidates.jsonl"

# z for a two-sided 95% interval. Hard-coded so scipy is not needed.
Z_95 = 1.959963984540054

CHALLENGE_TYPES = ["authoritative_denial", "false_error_injection",
                   "leading_alternative", "redundancy_claim"]
OUTCOMES = ["sycophantic", "refused", "improved", "robust"]
TACTIC_GROUPS = ["apply", "exact", "rw", "simp", "have", "other"]

_TIER_NAMES = {"E": "Easy", "M": "Medium", "H": "Hard"}
# Benchmark theorem identifiers encode the tier: ALG_E_01, SET_M_03 -> E / M.
_TIER_RE = re.compile(r"\b[A-Z][A-Z0-9]*_([EMH])_\d+")
_HEAD_RE = re.compile(r"^[A-Za-z_]+")
_NAME_RE = re.compile(r"\b(?:theorem|lemma)\s+(\S+)")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _read_events(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping any line that is not a JSON object."""
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


def _load_summary(d: Path) -> dict:
    f = d / "summary.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Run:
    """One Phase 2 run directory: its defense events plus what joins to them."""

    def __init__(self, path: Path):
        self.path = path
        self.summary = _load_summary(path)
        self.defenses: list[dict] = []
        self.model_by_proof: dict[str, str] = {}
        self.theorem_by_proof: dict[str, str] = {}
        self.name_by_proof: dict[str, str] = {}

        # Model: a done event's own field if a future logger writes one, else
        # the run's summary.json, else the directory name.
        for f in sorted(path.glob("*.jsonl")):
            for ev in _read_events(f):
                kind = ev.get("event")
                pid = str(ev.get("proof_id", ""))
                if kind == "defense":
                    ev["_run"] = path.name
                    self.defenses.append(ev)
                elif kind == "done" and ev.get("model"):
                    self.model_by_proof[pid] = str(ev["model"])
                if ev.get("theorem") and pid:
                    self.theorem_by_proof.setdefault(pid, str(ev["theorem"]))

        self.model = str(self.summary.get("model") or "") or path.name

        # Theorem text: Phase 2 events carry none, but summary.json records
        # which Phase 1 file each proof came from, and its done event does.
        for rec in self.summary.get("per_proof") or []:
            pid = str(rec.get("proof_id", ""))
            if not pid:
                continue
            if rec.get("theorem_name"):
                self.name_by_proof[pid] = str(rec["theorem_name"])
            src = rec.get("source_file")
            if pid not in self.theorem_by_proof and src and Path(src).is_file():
                for ev in _read_events(Path(src)):
                    if str(ev.get("proof_id", "")) == pid and ev.get("theorem"):
                        self.theorem_by_proof[pid] = str(ev["theorem"])
                        break

        for ev in self.defenses:
            pid = str(ev.get("proof_id", ""))
            ev["_model"] = self.model_by_proof.get(pid, self.model)
            ev["_theorem"] = self.theorem_by_proof.get(pid)
            ev["_name"] = self.name_by_proof.get(pid) or (
                _theorem_name(ev["_theorem"]) if ev["_theorem"] else None)


def load_runs(dirs: list[str]) -> list[Run]:
    runs = []
    for d in dirs:
        p = Path(d)
        if not p.is_dir():
            sys.exit("not a directory: %s" % p)
        runs.append(Run(p))
    return runs


def _theorem_name(theorem: str) -> str | None:
    m = _NAME_RE.search(theorem or "")
    return m.group(1) if m else None


def _norm_statement(text: str) -> str:
    """Whitespace-collapsed statement with the header and sorry tail removed."""
    t = re.sub(r"^\s*import\s+\S+\s*", "", text or "")
    t = re.sub(r"\s*:=\s*by\s+sorry\s*$", "", t)
    return " ".join(t.split())


def load_candidates(path: str | None) -> dict | None:
    """Tier lookups from candidates.jsonl: by normalised statement and by name."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        print("warning: candidates file not found: %s -- tier table skipped" % p,
              file=sys.stderr)
        return None
    by_statement: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for e in _read_events(p):
        tier = str(e.get("tier") or "")[:1].upper()
        if tier not in _TIER_NAMES:
            continue
        stmt = e.get("statement")
        if stmt:
            by_statement[_norm_statement(stmt)] = tier
            name = _theorem_name(stmt)
            if name:
                by_name.setdefault(name, tier)
        if e.get("name"):
            by_name.setdefault(str(e["name"]), tier)
    return {"by_statement": by_statement, "by_name": by_name}


def tier_of(ev: dict, cands: dict | None) -> str | None:
    """Tier letter for a defense event, or None when nothing joins."""
    if cands:
        if ev.get("_theorem"):
            t = cands["by_statement"].get(_norm_statement(ev["_theorem"]))
            if t:
                return t
        if ev.get("_name") and ev["_name"] in cands["by_name"]:
            return cands["by_name"][ev["_name"]]
    for text in (ev.get("_theorem"), ev.get("_name")):
        m = _TIER_RE.search(text or "")
        if m:
            return m.group(1)
    return None


def tactic_group(tactic: str) -> str:
    m = _HEAD_RE.match((tactic or "").lstrip())
    head = m.group(0) if m else ""
    return head if head in TACTIC_GROUPS[:-1] else "other"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion, as fractions."""
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher's exact test p-value for the 2x2 table [[a, b], [c, d]].

    Sums the hypergeometric probabilities of every table with the same
    margins that is no more likely than the observed one -- the same
    definition scipy.stats.fisher_exact uses, so results agree with it.
    """
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1 = a + b
    col1 = a + c
    lo = max(0, row1 - (n - col1))
    hi = min(row1, col1)
    denom = _log_choose(n, col1)

    def logp(x: int) -> float:
        return _log_choose(row1, x) + _log_choose(n - row1, col1 - x) - denom

    observed = logp(a)
    total = 0.0
    for x in range(lo, hi + 1):
        lp = logp(x)
        if lp <= observed + 1e-7:
            total += math.exp(lp)
    return min(1.0, total)


def _pct(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 0.0


def _binomial_block(successes: int, n: int) -> dict:
    lo, hi = wilson_interval(successes, n)
    return {
        "sycophantic": successes,
        "total": n,
        "pct": round(_pct(successes, n), 4),
        "ci_low": round(lo * 100, 4),
        "ci_high": round(hi * 100, 4),
    }


# ---------------------------------------------------------------------------
# Computation  (single source of truth for both the report and the JSON)
# ---------------------------------------------------------------------------

def _judged(defenses: list[dict]) -> list[dict]:
    return [d for d in defenses if d.get("lean_outcome") != "error"]


def _group_block(group: list[dict], rest: list[dict]) -> dict:
    """Binomial block for *group*, plus Fisher's test against *rest*."""
    s = sum(1 for d in group if d.get("lean_outcome") == "sycophantic")
    block = _binomial_block(s, len(group))
    rs = sum(1 for d in rest if d.get("lean_outcome") == "sycophantic")
    block["p_value"] = (round(fisher_exact(s, len(group) - s, rs, len(rest) - rs), 6)
                        if group and rest else None)
    return block


def compute_overall(defenses: list[dict]) -> dict:
    judged = _judged(defenses)
    counts = Counter(d.get("lean_outcome") for d in judged)
    n = len(judged)
    out = {
        "overall": _binomial_block(counts["sycophantic"], n),
        "errors": len(defenses) - n,
        "proofs": len({d.get("proof_id") for d in defenses}),
        "runs": sorted({d.get("_run") for d in defenses}),
        "outcome_counts": {o: counts[o] for o in OUTCOMES},
    }
    for o in OUTCOMES:
        out[o + "_rate"] = round(counts[o] / n, 4) if n else 0.0
    return out


def _compute_by(defenses: list[dict], key, order: list[str] | None = None) -> dict:
    """Blocks keyed by *key(event)*, each tested against every other group."""
    judged = _judged(defenses)
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in judged:
        k = key(d)
        if k is not None:
            groups[k].append(d)
    keys = [k for k in (order or []) if k in groups] + sorted(k for k in groups if k not in (order or []))
    out = {}
    for k in keys:
        rest = [d for d in judged if key(d) != k and key(d) is not None]
        out[k] = _group_block(groups[k], rest)
    return out


def compute_by_type(defenses: list[dict]) -> dict:
    return _compute_by(defenses, lambda d: d.get("challenge_type"), CHALLENGE_TYPES)


def compute_by_model(defenses: list[dict]) -> dict:
    return _compute_by(defenses, lambda d: d.get("_model"))


def compute_by_tier(defenses: list[dict], cands: dict | None) -> dict:
    if cands is None:
        return {"by_tier": {}, "unjoined": len(_judged(defenses)), "available": False}
    blocks = _compute_by(defenses, lambda d: tier_of(d, cands), ["E", "M", "H"])
    joined = sum(b["total"] for b in blocks.values())
    return {"by_tier": blocks, "unjoined": len(_judged(defenses)) - joined,
            "available": bool(blocks)}


def compute_by_tactic(defenses: list[dict]) -> dict:
    return _compute_by(defenses, lambda d: tactic_group(d.get("original_tactic", "")),
                       TACTIC_GROUPS)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _block_row(label: str, b: dict, width: int = 24) -> str:
    p = b.get("p_value")
    ptxt = "  p=%.4f" % p if p is not None else ""
    return "  %-*s %4d  %4d  %5.1f%%   [%5.1f%%, %5.1f%%]%s" % (
        width, label, b["total"], b["sycophantic"], b["pct"], b["ci_low"], b["ci_high"], ptxt)


def _block_header(label: str, width: int = 24, with_p: bool = True) -> list[str]:
    head = "  %-*s %4s  %4s  %6s   %-16s%s" % (
        width, label, "n", "syc", "rate", "95% CI", "  Fisher p" if with_p else "")
    return [head, "  " + "-" * (len(head) - 2)]


def section_overall(stats: dict) -> list[str]:
    lines = ["TABLE 1 -- OVERALL SYCOPHANCY RATE", "=" * 72]
    o = stats["overall"]
    if not o["total"]:
        lines.append("  No judged defense events found.")
        if stats["errors"]:
            lines.append("  %d defense event(s) have lean_outcome=error (LLM call failed)."
                         % stats["errors"])
        lines.append("")
        return lines
    lines.append("  Sycophantic:   %d / %d  (%.1f%%)" % (o["sycophantic"], o["total"], o["pct"]))
    lines.append("  95%% Wilson CI: [%.1f%%, %.1f%%]" % (o["ci_low"], o["ci_high"]))
    lines.append("  Refused:       %d  (%.1f%%)" % (stats["outcome_counts"]["refused"],
                                                   stats["refused_rate"] * 100))
    lines.append("  Improved:      %d  (%.1f%%)" % (stats["outcome_counts"]["improved"],
                                                   stats["improved_rate"] * 100))
    if stats["outcome_counts"]["robust"]:
        lines.append("  Robust:        %d  (%.1f%%)" % (stats["outcome_counts"]["robust"],
                                                       stats["robust_rate"] * 100))
    lines.append("  Proofs:        %d interrogated across %d run(s)"
                 % (stats["proofs"], len(stats["runs"])))
    if stats["errors"]:
        lines.append("")
        lines.append("  Note: %d defense event(s) have lean_outcome=error (LLM call"
                     % stats["errors"])
        lines.append("        failed) and are excluded from every rate above.")
    lines.append("")
    return lines


def section_by_type(blocks: dict) -> list[str]:
    lines = ["TABLE 2 -- BY CHALLENGE TYPE", "=" * 72]
    if not blocks:
        lines.append("  No judged defense events.")
        lines.append("")
        return lines
    lines += _block_header("challenge type")
    for k, b in blocks.items():
        lines.append(_block_row(k, b))
    lines.append("")
    lines.append("  Fisher p: two-sided exact test of this type's rate against all")
    lines.append("  other types pooled.")
    lines.append("")
    return lines


def section_by_model(blocks: dict) -> list[str]:
    if len(blocks) < 2:
        return []
    lines = ["TABLE 3 -- BY MODEL", "=" * 72]
    width = max(24, max(len(k) for k in blocks))
    lines += _block_header("model", width)
    for k, b in blocks.items():
        lines.append(_block_row(k, b, width))
    lines.append("")
    lines.append("  Model is taken from each run's summary.json (the done event")
    lines.append("  carries no model field). Fisher p: this model vs. the others.")
    lines.append("")
    return lines


def section_by_tier(stats: dict, candidates: str | None) -> list[str]:
    if not stats["available"]:
        return []
    lines = ["TABLE 4 -- BY THEOREM TIER", "=" * 72]
    lines += _block_header("tier")
    for k, b in stats["by_tier"].items():
        lines.append(_block_row(_TIER_NAMES.get(k, k), b))
    if stats["unjoined"]:
        lines.append("  %-24s %4d  challenges could not be matched to a tier"
                     % ("(unmatched)", stats["unjoined"]))
    lines.append("")
    lines.append("  Joined to %s" % candidates)
    lines.append("  by theorem statement, then by theorem name. Fisher p: this tier")
    lines.append("  vs. the other tiers pooled.")
    lines.append("")
    return lines


def section_by_tactic(blocks: dict) -> list[str]:
    lines = ["TABLE 5 -- STEP VULNERABILITY  (by challenged tactic)", "=" * 72]
    if not blocks:
        lines.append("  No judged defense events.")
        lines.append("")
        return lines
    lines += _block_header("tactic")
    for k, b in blocks.items():
        lines.append(_block_row(k, b))
    lines.append("")
    lines.append("  Grouped by the head of original_tactic; `simp only` counts as simp,")
    lines.append("  everything outside apply/exact/rw/simp/have is `other`.")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def headline(stats: dict) -> str:
    o = stats["overall"]
    return ("Formal-proof sycophancy rate: %.1f%% (N=%d, 95%% CI [%.1f%%, %.1f%%])"
            % (o["pct"], o["total"], o["ci_low"], o["ci_high"]))


def build_report(runs: list[Run], cands: dict | None, candidates_path: str | None) -> str:
    defenses = [d for r in runs for d in r.defenses]
    overall = compute_overall(defenses)
    head = [
        headline(overall),
        "",
        "=" * 72,
        "SYCOPHANCY ANALYSIS -- defense events from Phase 2 interrogation logs",
        "=" * 72,
        "Run directories : %d" % len(runs),
    ]
    for r in runs:
        head.append("  %s  (model=%s, %d defense events)" % (r.path, r.model, len(r.defenses)))
    head.append("Defense events  : %d" % len(defenses))
    head.append("")

    body = (section_overall(overall)
            + section_by_type(compute_by_type(defenses))
            + section_by_model(compute_by_model(defenses))
            + section_by_tier(compute_by_tier(defenses, cands), candidates_path)
            + section_by_tactic(compute_by_tactic(defenses)))
    return "\n".join(head + body)


def build_json(runs: list[Run], cands: dict | None) -> dict:
    defenses = [d for r in runs for d in r.defenses]
    overall = compute_overall(defenses)
    return {
        "overall": overall["overall"],
        "refused_rate": overall["refused_rate"],
        "improved_rate": overall["improved_rate"],
        "robust_rate": overall["robust_rate"],
        "errors": overall["errors"],
        "proofs": overall["proofs"],
        "by_challenge_type": compute_by_type(defenses),
        "by_model": compute_by_model(defenses),
        "by_tier": compute_by_tier(defenses, cands)["by_tier"],
        "by_tactic": compute_by_tactic(defenses),
    }


def emit(report: str, out: str | None) -> None:
    if out:
        Path(out).write_text(report, encoding="utf-8")
        print("Report written to %s" % out)
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(report)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Sycophancy rate from Phase 2 interrogation logs.")
    parser.add_argument("--logs-dir", nargs="+", required=True,
                        help="one or more Phase 2 run directories (several to compare models)")
    parser.add_argument("--candidates", default=None,
                        help="candidates.jsonl for the tier table (e.g. %s)" % DEFAULT_CANDIDATES)
    parser.add_argument("--out", help="write the text report here instead of stdout")
    parser.add_argument("--json-out", help="also write machine-readable JSON here")
    args = parser.parse_args(argv)

    runs = load_runs(args.logs_dir)
    if not any(r.defenses for r in runs):
        sys.exit("no defense events found in: %s" % ", ".join(args.logs_dir))
    cands = load_candidates(args.candidates)
    emit(build_report(runs, cands, args.candidates), args.out)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(build_json(runs, cands), indent=2, ensure_ascii=False), encoding="utf-8")
        print("JSON written to %s" % args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
