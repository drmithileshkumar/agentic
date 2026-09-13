"""
analyze.py -- pass rate, step economy and tactic distribution (RQ1 + RQ3 + RQ5).

Reads the proof_XXXX.jsonl files (and summary.json, when present) written by
the harness into a run directory under C:\\lean\\logs\\.

Usage
-----
    python analyze.py --logs-dir C:\\lean\\logs\\20260906_191021
    python analyze.py --all-runs
    python analyze.py --all-runs --out report.txt
    python analyze.py --logs-dir <dir> --json-out stats.json

Units convention shared by every script in this set:
    *_pct, ci_low, ci_high   percentages, 0-100
    *_rate, precision        fractions, 0-1

Stdlib only -- no pandas, no scipy, no matplotlib.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_LOG_BASE = r"C:\lean\logs"

# z for a two-sided 95% interval. Hard-coded so scipy is not needed.
Z_95 = 1.959963984540054

# Tier is not recorded in the start event, but the benchmark's own theorem
# identifiers encode it: ALG_E_01, SET_M_03, NUM_H_02 -> E / M / H.
_TIER_RE = re.compile(r"\b[A-Z][A-Z0-9]*_([EMH])_\d+")
_TIER_NAMES = {"E": "Easy", "M": "Medium", "H": "Hard"}

BASELINE_NOTE = "SorryDB agentic baseline: ~20-30% Easy"

HISTOGRAM_STEPS = 15      # histogram always spans at least this many steps
PRECISION_MIN_USES = 3    # a tactic needs this many uses to get a precision
RELIABLE_CLOSER = 0.8     # precision above this earns the "reliable closer" tag


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class Proof:
    """One proof attempt, assembled from its JSONL event stream."""

    def __init__(self, path: Path):
        self.path = path
        self.start: dict | None = None
        self.done: dict | None = None
        self.steps: list[dict] = []
        self.errors: list[dict] = []

        for event in _read_events(path):
            kind = event.get("event")
            if kind == "start":
                self.start = event
            elif kind == "step":
                self.steps.append(event)
            elif kind == "done":
                self.done = event
            elif kind == "error":
                self.errors.append(event)

        self.steps.sort(key=lambda e: e.get("step") or 0)

    @property
    def proof_id(self) -> str:
        for src in (self.done, self.start):
            if src and src.get("proof_id"):
                return str(src["proof_id"])
        for step in self.steps:
            if step.get("proof_id"):
                return str(step["proof_id"])
        return self.path.stem

    @property
    def theorem(self) -> str:
        for src in (self.start, self.done):
            if src and src.get("theorem"):
                return str(src["theorem"])
        for step in self.steps:
            if step.get("theorem"):
                return str(step["theorem"])
        return ""

    @property
    def result(self) -> str | None:
        """PROVED / FAILED_* -- or None when the run was cut off mid-proof."""
        if self.done:
            return self.done.get("result")
        return None

    @property
    def proved(self) -> bool:
        return self.result == "PROVED"

    @property
    def total_steps(self) -> int:
        if self.done and isinstance(self.done.get("total_steps"), int):
            return self.done["total_steps"]
        return len(self.steps)

    @property
    def tier(self) -> str | None:
        """Tier letter, from the start event if present, else the theorem id."""
        if self.start and self.start.get("tier"):
            return str(self.start["tier"])[:1].upper()
        match = _TIER_RE.search(self.theorem)
        return match.group(1) if match else None

    @property
    def dominant_outcome(self) -> str | None:
        counts = Counter(s.get("outcome") for s in self.steps if s.get("outcome"))
        return counts.most_common(1)[0][0] if counts else None


def _read_events(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping any line that is not a JSON object.

    A run killed mid-write can leave a truncated final line; that should cost
    one event, not the whole file.
    """
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


def resolve_dirs(args) -> list[Path]:
    if args.all_runs:
        base = Path(args.log_base)
        if not base.is_dir():
            sys.exit("log base directory not found: %s" % base)
        dirs = sorted(d for d in base.iterdir() if d.is_dir())
        if not dirs:
            sys.exit("no run directories under %s" % base)
        return dirs
    if not args.logs_dir:
        sys.exit("give --logs-dir <run directory> or --all-runs")
    d = Path(args.logs_dir)
    if not d.is_dir():
        sys.exit("not a directory: %s" % d)
    return [d]


def load_proofs(dirs: list[Path]) -> list[Proof]:
    proofs = []
    for d in dirs:
        for f in sorted(d.glob("proof_*.jsonl")):
            proofs.append(Proof(f))
    return proofs


def load_summaries(dirs: list[Path]) -> list[dict]:
    out = []
    for d in dirs:
        f = d / "summary.json"
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["_dir"] = d.name
            out.append(data)
    return out


def emit(report: str, out: str | None) -> None:
    if out:
        Path(out).write_text(report, encoding="utf-8")
        print("Report written to %s" % out)
    else:
        # Goal states carry Unicode (N, |-); a cp1252 console would otherwise
        # raise UnicodeEncodeError and lose the whole report.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(report)


def emit_json(payload: dict, path: str) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    print("JSON written to %s" % path)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion, as fractions.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves at small n and at proportions near 0 or 1 -- both of which this
    benchmark hits routinely.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _pct(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 0.0


def _short(text: str, width: int = 46) -> str:
    """One-line, length-capped rendering of a tactic for table display."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "\u2026"


def _binomial_block(successes: int, n: int) -> dict:
    lo, hi = wilson_interval(successes, n)
    return {
        "proved": successes,
        "total": n,
        "pct": round(_pct(successes, n), 4),
        "ci_low": round(lo * 100, 4),
        "ci_high": round(hi * 100, 4),
    }


# ---------------------------------------------------------------------------
# Computation  (single source of truth for both the report and the JSON)
# ---------------------------------------------------------------------------

def compute_pass_rate(proofs: list[Proof]) -> dict:
    finished = [p for p in proofs if p.result is not None]
    proved = [p for p in finished if p.proved]

    by_tier = {}
    grouped = defaultdict(list)
    for p in finished:
        if p.tier:
            grouped[p.tier].append(p)
    for letter in ("E", "M", "H"):
        group = grouped.get(letter)
        if not group:                       # omit tiers with no proofs at all
            continue
        by_tier[letter] = _binomial_block(sum(1 for p in group if p.proved), len(group))

    return {
        "overall": _binomial_block(len(proved), len(finished)),
        "by_tier": by_tier,
        "untiered": sum(1 for p in finished if not p.tier),
        "incomplete": sum(1 for p in proofs if p.result is None),
        "failure_codes": Counter(p.result for p in finished if not p.proved),
    }


def compute_step_economy(proofs: list[Proof]) -> dict:
    steps = sorted(p.total_steps for p in proofs if p.proved)
    if not steps:
        return {"count": 0, "min": None, "median": None, "mean": None,
                "max": None, "histogram": {}, "last_closing_step": None}
    hist = Counter(steps)
    top = max(HISTOGRAM_STEPS, steps[-1])
    return {
        "count": len(steps),
        "min": steps[0],
        "median": round(statistics.median(steps), 4),
        "mean": round(statistics.fmean(steps), 4),
        "max": steps[-1],
        "histogram": {str(i): hist.get(i, 0) for i in range(1, top + 1)},
        "last_closing_step": steps[-1],
    }


def compute_tactics(proofs: list[Proof], min_uses: int = PRECISION_MIN_USES) -> dict:
    uses: Counter = Counter()
    wins: Counter = Counter()
    for p in proofs:
        for step in p.steps:
            tactic = step.get("tactic")
            if not isinstance(tactic, str) or not tactic.strip():
                continue
            uses[tactic] += 1
            if p.proved and step.get("outcome") == "proved":
                wins[tactic] += 1

    total = sum(uses.values())
    freq = [{"tactic": t, "count": n, "pct": round(_pct(n, total), 4)}
            for t, n in uses.most_common(20)]

    eligible = []
    for tactic, n in uses.items():
        if n < min_uses:
            continue
        won = wins.get(tactic, 0)
        precision = won / n
        eligible.append({
            "tactic": tactic,
            "uses": n,
            "wins": won,
            "precision": round(precision, 4),
            "reliable_closer": precision > RELIABLE_CLOSER,
        })
    eligible.sort(key=lambda row: (-row["precision"], -row["uses"], row["tactic"]))

    return {
        "distinct": len(uses),
        "total_uses": total,
        "freq": freq,
        "precision": eligible[:10],
        "min_uses": min_uses,
    }


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_pass_rate(stats: dict, summaries: list[dict]) -> list[str]:
    lines = ["PASS RATE", "=" * 72]
    overall = stats["overall"]

    if not overall["total"]:
        lines.append("  No completed proofs found.")
        if stats["incomplete"]:
            lines.append("  %d proof file(s) have no done event (run cut off)."
                         % stats["incomplete"])
        lines.append("")
        return lines

    lines.append("  Proved:        %d / %d  (%.1f%%)"
                 % (overall["proved"], overall["total"], overall["pct"]))
    lines.append("  Failed:        %d" % (overall["total"] - overall["proved"]))
    lines.append("  95%% Wilson CI: [%.1f%%, %.1f%%]"
                 % (overall["ci_low"], overall["ci_high"]))
    lines.append("  Comparison:    %s" % BASELINE_NOTE)

    if stats["failure_codes"]:
        lines.append("")
        lines.append("  Failure result codes:")
        for code, count in stats["failure_codes"].most_common():
            lines.append("    %-28s %3d  (%.1f%%)"
                         % (code, count, _pct(count, overall["total"])))

    lines.append("")
    if stats["by_tier"]:
        lines.append("  Per tier (inferred from the theorem identifier -- the start")
        lines.append("  event carries no tier field):")
        for letter in ("E", "M", "H"):
            block = stats["by_tier"].get(letter)
            if not block:
                continue
            lines.append("    %-7s %3d / %3d  (%5.1f%%)   95%% CI [%.1f%%, %.1f%%]"
                         % (_TIER_NAMES[letter], block["proved"], block["total"],
                            block["pct"], block["ci_low"], block["ci_high"]))
        if stats["untiered"]:
            lines.append("    %-7s %3d proofs had no identifiable tier"
                         % ("(none)", stats["untiered"]))
    else:
        lines.append("  Per tier: no tier information available -- overall only.")

    if stats["incomplete"]:
        lines.append("")
        lines.append("  Note: %d proof file(s) have no done event and are excluded"
                     % stats["incomplete"])
        lines.append("        from every figure above (run stopped mid-proof).")

    if summaries:
        models = sorted({str(s.get("model")) for s in summaries if s.get("model")})
        budgets = sorted({s.get("max_steps") for s in summaries if s.get("max_steps")})
        lines.append("")
        lines.append("  From summary.json: model=%s  max_steps=%s  runs=%d"
                     % (", ".join(models) or "?",
                        ", ".join(str(b) for b in budgets) or "?", len(summaries)))
    lines.append("")
    return lines


def section_step_economy(econ: dict) -> list[str]:
    lines = ["STEP ECONOMY  (proved proofs only)", "=" * 72]
    if not econ["count"]:
        lines.append("  No proved proofs -- nothing to measure.")
        lines.append("")
        return lines

    lines.append("  Proofs proved: %d" % econ["count"])
    lines.append("  Steps to proof -- min %d   median %.1f   mean %.2f   max %d"
                 % (econ["min"], econ["median"], econ["mean"], econ["max"]))
    lines.append("")

    hist = econ["histogram"]
    peak = max(hist.values()) if hist else 0
    lines.append("  Proofs closed at each step:")
    for key, count in hist.items():
        bar = "#" * int(round(30 * count / peak)) if peak else ""
        lines.append("    step %2s | %3d  %5.1f%%  %s"
                     % (key, count, _pct(count, econ["count"]), bar))

    lines.append("")
    last = econ["last_closing_step"]
    if last >= len(hist):
        lines.append("  Point of no return: proofs were still closing at the last")
        lines.append("  observed step (%d) -- no cut-off is visible in this data." % last)
    else:
        lines.append("  Point of no return: step %d. No proof in this data closed" % last)
        lines.append("  after step %d; steps %d+ produced 0 proofs." % (last, last + 1))
    lines.append("")
    return lines


def section_tactics(tac: dict) -> list[str]:
    lines = ["TACTIC DISTRIBUTION  (all proofs)", "=" * 72]
    if not tac["total_uses"]:
        lines.append("  No tactics recorded.")
        lines.append("")
        return lines

    lines.append("  Distinct tactics: %d over %d uses"
                 % (tac["distinct"], tac["total_uses"]))
    lines.append("")
    lines.append("  Top 20 by frequency:")
    lines.append("    %-46s %5s  %6s" % ("tactic", "uses", "share"))
    lines.append("    " + "-" * 60)
    for row in tac["freq"]:
        lines.append("    %-46s %5d  %5.1f%%"
                     % (_short(row["tactic"]), row["count"], row["pct"]))

    lines.append("")
    if not tac["precision"]:
        lines.append("  Precision: no tactic reached the %d-use minimum." % tac["min_uses"])
        lines.append("")
        return lines

    lines.append("  Top 10 by precision  (closes / uses, minimum %d uses):"
                 % tac["min_uses"])
    lines.append("    %-46s %9s %6s" % ("tactic", "closes", "prec"))
    lines.append("    " + "-" * 64)
    for row in tac["precision"]:
        lines.append("    %-46s %4d/%-4d %5.2f"
                     % (_short(row["tactic"]), row["wins"], row["uses"], row["precision"]))

    reliable = [r for r in tac["precision"] if r["reliable_closer"]]
    lines.append("")
    if reliable:
        lines.append("  Reliable closers (precision > %.2f):" % RELIABLE_CLOSER)
        for row in reliable:
            lines.append("    %-46s %.2f  (%d/%d)"
                         % (_short(row["tactic"]), row["precision"],
                            row["wins"], row["uses"]))
    else:
        lines.append("  Reliable closers (precision > %.2f): none." % RELIABLE_CLOSER)
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_report(proofs: list[Proof], summaries: list[dict], dirs: list[Path]) -> str:
    head = [
        "=" * 72,
        "HARNESS ANALYSIS -- pass rate, step economy, tactic distribution",
        "=" * 72,
        "Run directories : %d" % len(dirs),
    ]
    for d in dirs:
        head.append("  %s" % d)
    head.append("Proof files     : %d" % len(proofs))
    head.append("")

    body = (section_pass_rate(compute_pass_rate(proofs), summaries)
            + section_step_economy(compute_step_economy(proofs))
            + section_tactics(compute_tactics(proofs)))
    return "\n".join(head + body)


def build_json(proofs: list[Proof]) -> dict:
    """Machine-readable form of the same numbers the report renders."""
    stats = compute_pass_rate(proofs)
    econ = compute_step_economy(proofs)
    tac = compute_tactics(proofs)
    return {
        "pass_rate": stats["overall"],
        "by_tier": stats["by_tier"],
        "step_economy": {
            "min": econ["min"],
            "median": econ["median"],
            "mean": econ["mean"],
            "max": econ["max"],
            "histogram": econ["histogram"],
        },
        "tactic_freq": tac["freq"],
        "tactic_precision": tac["precision"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Pass rate, step economy and tactic distribution "
                    "for harness proof logs (RQ1 + RQ3 + RQ5).")
    parser.add_argument("--logs-dir", help="a single run directory to analyse")
    parser.add_argument("--all-runs", action="store_true",
                        help="merge every run directory under --log-base")
    parser.add_argument("--log-base", default=DEFAULT_LOG_BASE,
                        help="root holding run directories (default: %s)" % DEFAULT_LOG_BASE)
    parser.add_argument("--out", help="write the text report here instead of stdout")
    parser.add_argument("--json-out", help="also write machine-readable JSON here")
    args = parser.parse_args(argv)

    dirs = resolve_dirs(args)
    proofs = load_proofs(dirs)
    if not proofs:
        sys.exit("no proof_*.jsonl files found in: %s"
                 % ", ".join(str(d) for d in dirs))
    emit(build_report(proofs, load_summaries(dirs), dirs), args.out)
    if args.json_out:
        emit_json(build_json(proofs), args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
