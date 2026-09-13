"""
history_analysis.py -- does the agent use its history productively? (RQ4)

Reads the proof_XXXX.jsonl files in a run directory and reports:

  Repetition rate    per proof, the fraction of steps whose tactic already
                     appeared earlier in that same proof; mean +/- std across
                     proofs. High values mean the history is being ignored.

  Advanced-rate      by step number, the fraction of step events at that step
                     with outcome == "advanced", as an ASCII line chart. A
                     falling curve means later steps buy less.

  Goal shrinkage     mean character count of goal_before at steps 1, 3, 6 and
                     10, for proved and failed proofs separately. A proof that
                     is working should be shrinking its goal.

Usage
-----
    python history_analysis.py --logs-dir C:\\lean\\logs\\20260906_191021
    python history_analysis.py --all-runs
    python history_analysis.py --all-runs --out history.txt

Stdlib only -- no pandas, no scipy, no matplotlib.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULT_LOG_BASE = r"C:\lean\logs"

MAX_STEP = 15                 # x-axis extent of the advanced-rate chart
CHART_WIDTH = 40              # plot area width, in characters
CHART_ROWS = 10               # rows between 100% and 0%
SHRINK_PROBES = (1, 3, 6, 10)  # step numbers sampled for goal shrinkage


# ---------------------------------------------------------------------------
# Loading  (kept local so this script runs standalone)
# ---------------------------------------------------------------------------

class Proof:
    def __init__(self, path: Path):
        self.path = path
        self.start: dict | None = None
        self.done: dict | None = None
        self.steps: list[dict] = []

        for event in _read_events(path):
            kind = event.get("event")
            if kind == "start":
                self.start = event
            elif kind == "step":
                self.steps.append(event)
            elif kind == "done":
                self.done = event
        self.steps.sort(key=lambda e: e.get("step") or 0)

    @property
    def result(self) -> str | None:
        return self.done.get("result") if self.done else None

    @property
    def proved(self) -> bool:
        return self.result == "PROVED"


def _read_events(path: Path) -> list[dict]:
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


def emit_json(payload: dict, path: str) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    print("JSON written to %s" % path)


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], 0.0)
    return (statistics.fmean(values), statistics.stdev(values))


# ---------------------------------------------------------------------------
# Repetition
# ---------------------------------------------------------------------------

def repetition_rates(proofs: list[Proof]) -> list[tuple[Proof, float, int, int]]:
    """(proof, rate, repeats, steps) for every proof with at least one step."""
    rows = []
    for proof in proofs:
        tactics = [str(s.get("tactic") or "") for s in proof.steps]
        tactics = [t for t in tactics if t.strip()]
        if not tactics:
            continue
        seen: set[str] = set()
        repeats = 0
        for tactic in tactics:
            if tactic in seen:
                repeats += 1
            else:
                seen.add(tactic)
        rows.append((proof, repeats / len(tactics), repeats, len(tactics)))
    return rows


def section_repetition(proofs: list[Proof]) -> list[str]:
    lines = ["REPETITION RATE", "=" * 72,
             "  Fraction of a proof's steps whose tactic already appeared",
             "  earlier in the same proof.", ""]

    rows = repetition_rates(proofs)
    if not rows:
        lines.append("  No proofs with step events.")
        lines.append("")
        return lines

    rates = [r for _, r, _, _ in rows]
    mean, std = _mean_std(rates)
    lines.append("  Proofs measured : %d" % len(rows))
    lines.append("  Repetition rate : %.3f +/- %.3f  (mean +/- std)" % (mean, std))
    lines.append("  Range           : %.3f .. %.3f" % (min(rates), max(rates)))

    total_steps = sum(n for _, _, _, n in rows)
    total_repeats = sum(r for _, _, r, _ in rows)
    lines.append("  Pooled          : %d repeated of %d steps  (%.1f%%)"
                 % (total_repeats, total_steps,
                    100.0 * total_repeats / total_steps if total_steps else 0.0))

    worst = sorted(rows, key=lambda row: (-row[1], -row[3]))[:5]
    repeating = [row for row in worst if row[2]]
    if repeating:
        lines.append("")
        lines.append("  Most repetitive proofs:")
        for proof, rate, repeats, count in repeating:
            lines.append("    %-22s %.2f  (%d of %d steps repeated)  %s"
                         % (proof.path.name, rate, repeats, count,
                            proof.result or "incomplete"))
    else:
        lines.append("")
        lines.append("  No proof repeated a tactic.")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Advanced-rate chart
# ---------------------------------------------------------------------------

def advanced_rates(proofs: list[Proof], max_step: int = MAX_STEP):
    """(rate, advanced, total) per step number 1..max_step."""
    advanced = [0] * (max_step + 1)
    total = [0] * (max_step + 1)
    for proof in proofs:
        for step in proof.steps:
            number = step.get("step")
            if not isinstance(number, int) or not (1 <= number <= max_step):
                continue
            total[number] += 1
            if step.get("outcome") == "advanced":
                advanced[number] += 1
    return [(advanced[i] / total[i] if total[i] else None, advanced[i], total[i])
            for i in range(1, max_step + 1)]


def ascii_line_chart(series, max_step: int = MAX_STEP,
                     width: int = CHART_WIDTH, rows: int = CHART_ROWS) -> list[str]:
    """Line chart of rate vs step: x = step 1..max_step, y = 0..100%."""
    columns = [None] * width
    for index, (rate, _, _) in enumerate(series):
        if rate is None:
            continue
        step_pos = 0 if max_step == 1 else round(index * (width - 1) / (max_step - 1))
        columns[step_pos] = rate

    lines = []
    for row in range(rows, -1, -1):
        upper = row / rows
        lower = (row - 0.5) / rows
        cells = []
        for value in columns:
            if value is None:
                cells.append(" ")
            elif value <= upper and value > lower:
                cells.append("*")
            elif row == 0 and value <= upper:
                cells.append("*")
            else:
                cells.append(" ")
        lines.append("  %3d%% |%s" % (int(upper * 100), "".join(cells)))
    lines.append("       +" + "-" * width)

    axis = [" "] * width
    for step in (1, 5, 10, max_step):
        if step > max_step:
            continue
        pos = 0 if max_step == 1 else round((step - 1) * (width - 1) / (max_step - 1))
        label = str(step)
        start = min(max(pos - (len(label) - 1), 0), width - len(label))
        for offset, char in enumerate(label):
            axis[start + offset] = char
    lines.append("        " + "".join(axis) + "   step")
    return lines


def section_advanced(proofs: list[Proof]) -> list[str]:
    lines = ["ADVANCED-RATE BY STEP NUMBER", "=" * 72,
             "  Fraction of step events at each step number whose outcome",
             "  was \"advanced\".", ""]

    series = advanced_rates(proofs)
    if not any(total for _, _, total in series):
        lines.append("  No step events in range 1..%d." % MAX_STEP)
        lines.append("")
        return lines

    lines.extend(ascii_line_chart(series))
    lines.append("")
    lines.append("  step | advanced / total |  rate")
    lines.append("  " + "-" * 38)
    for index, (rate, adv, total) in enumerate(series, start=1):
        if not total:
            lines.append("  %4d | %8s         |     --" % (index, "0/0"))
        else:
            lines.append("  %4d | %6d / %-6d |  %5.1f%%"
                         % (index, adv, total, rate * 100))
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Goal shrinkage
# ---------------------------------------------------------------------------

def _goal_length_at(proof: Proof, target: int) -> int | None:
    """Length of goal_before at *target*, or at the last step if it ended sooner.

    Returns None when the proof has no step carrying a usable goal_before.
    """
    usable = [s for s in proof.steps if isinstance(s.get("goal_before"), str)]
    if not usable:
        return None
    chosen = None
    for step in usable:
        number = step.get("step")
        if isinstance(number, int) and number <= target:
            chosen = step
        elif isinstance(number, int) and number > target:
            break
    if chosen is None:
        chosen = usable[0] if (usable[0].get("step") or 1) <= target else None
        if chosen is None:
            return None
    return len(chosen["goal_before"])


def compute_shrinkage(proofs: list[Proof]) -> dict:
    """Mean goal_before length at each probe step, split proved vs failed."""
    groups = {
        "proved": [p for p in proofs if p.proved],
        "failed": [p for p in proofs if p.result is not None and not p.proved],
    }
    out = {}
    for label, group in groups.items():
        measurable = [p for p in group
                      if _goal_length_at(p, SHRINK_PROBES[0]) is not None]
        row = {}
        for probe in SHRINK_PROBES:
            values = [v for v in (_goal_length_at(p, probe) for p in measurable)
                      if v is not None]
            row[str(probe)] = round(statistics.fmean(values), 2) if values else None
        out[label] = row
        out[label + "_n"] = len(measurable)
    return out


def section_shrinkage(proofs: list[Proof]) -> list[str]:
    lines = ["GOAL SHRINKAGE", "=" * 72,
             "  Mean character count of goal_before at each probe step. Where a",
             "  proof ended before the probe, its last available step is used,",
             "  so every group keeps the same membership across columns.", ""]

    shrink = compute_shrinkage(proofs)
    incomplete = [p for p in proofs if p.result is None]

    header = "  %-8s %7s" % ("group", "n")
    for probe in SHRINK_PROBES:
        header += " %10s" % ("step %d" % probe)
    lines.append(header)
    lines.append("  " + "-" * (17 + 11 * len(SHRINK_PROBES)))

    any_data = False
    for label in ("proved", "failed"):
        row = "  %-8s %7d" % (label.upper(), shrink[label + "_n"])
        for probe in SHRINK_PROBES:
            value = shrink[label][str(probe)]
            row += " %10s" % ("%.0f" % value if value is not None else "--")
            if value is not None:
                any_data = True
        lines.append(row)

    if not any_data:
        lines.append("")
        lines.append("  No goal_before data available.")
    else:
        lines.append("")
        lines.append("  A flat or rising row means the goal is not shrinking: the")
        lines.append("  agent is churning rather than decomposing the problem.")
    if incomplete:
        lines.append("  (%d incomplete proof(s) excluded -- no done event.)" % len(incomplete))
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_json(proofs: list[Proof]) -> dict:
    """Machine-readable form of the same numbers the report renders.

    Rates are fractions (0-1); advanced-rate is null at a step with no events,
    so a chart can skip it rather than plot a false zero.
    """
    rates = [r for _, r, _, _ in repetition_rates(proofs)]
    mean, std = _mean_std(rates)
    series = advanced_rates(proofs)
    shrink = compute_shrinkage(proofs)
    return {
        "repetition_rate": {"mean": round(mean, 4), "std": round(std, 4)},
        "advanced_rate_by_step": {
            str(i): (round(rate, 4) if rate is not None else None)
            for i, (rate, _, _) in enumerate(series, start=1)
        },
        "goal_shrinkage": {
            "proved": shrink["proved"],
            "failed": shrink["failed"],
        },
    }


def build_report(proofs: list[Proof], dirs: list[Path]) -> str:
    head = [
        "=" * 72,
        "HISTORY UTILISATION  (RQ4)",
        "=" * 72,
        "Run directories : %d" % len(dirs),
    ]
    for d in dirs:
        head.append("  %s" % d)
    proved = sum(1 for p in proofs if p.proved)
    finished = sum(1 for p in proofs if p.result is not None)
    head.append("Proof files     : %d  (completed %d, proved %d)"
                % (len(proofs), finished, proved))
    head.append("")

    body = (section_repetition(proofs)
            + section_advanced(proofs)
            + section_shrinkage(proofs))
    return "\n".join(head + body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="History utilisation: repetition, advanced-rate by step, "
                    "and goal shrinkage (RQ4).")
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
    emit(build_report(proofs, dirs), args.out)
    if args.json_out:
        emit_json(build_json(proofs), args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
