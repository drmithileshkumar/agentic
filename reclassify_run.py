"""
reclassify_run.py -- re-score RQ4 against corrected step outcomes (read-only).

Until commit dd33000 the harness decided a step had failed by looking for the
literal word "error" in Lean's messages. Lean says `Unknown identifier` and
`Unknown constant` without ever using it, so failed tactics were logged as
`advanced` and the harness adopted the broken proof state they returned. The
fix only affects runs made after it; logs already on disk still carry the old
labels.

This script applies the corrected rule to existing logs and prints the RQ4
numbers both ways, so the size of the distortion is visible.

    a step is reclassified advanced -> error when its lean_messages contain any
    of: error, unknown, expected, failed, invalid   (case-insensitive)

Nothing is written back. The JSONL files are opened read-only and the
corrections live in memory for the duration of the run.

Usage
-----
    python reclassify_run.py C:\\lean\\logs\\20260909_002719
    python reclassify_run.py --logs-dir C:\\lean\\logs\\20260909_002719
    python reclassify_run.py --all-runs
    python reclassify_run.py <dir> --out corrected_rq4.txt

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULT_LOG_BASE = r"C:\lean\logs"

# Kept in sync with harness.lean_agent.LEAN_FAILURE_KEYWORDS. Imported from
# there when the harness is importable, so the two cannot drift; the literal
# below is the fallback for running this against logs copied elsewhere.
FALLBACK_KEYWORDS = ("error", "unknown", "expected", "failed", "invalid")

MAX_STEP = 15
SHRINK_PROBES = (1, 3, 6, 10)


def failure_keywords() -> tuple:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from harness.lean_agent import LEAN_FAILURE_KEYWORDS  # noqa: E402
        return tuple(LEAN_FAILURE_KEYWORDS)
    except Exception:
        return FALLBACK_KEYWORDS


KEYWORDS = failure_keywords()


# ---------------------------------------------------------------------------
# Loading  (read-only)
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


def messages_report_failure(messages) -> bool:
    for message in messages or []:
        low = str(message).lower()
        if any(k in low for k in KEYWORDS):
            return True
    return False


class Proof:
    """One proof, carrying both the original and the corrected outcomes."""

    def __init__(self, path: Path):
        self.path = path
        self.done = None
        self.steps: list[dict] = []
        for event in read_events(path):
            kind = event.get("event")
            if kind == "step":
                self.steps.append(event)
            elif kind == "done":
                self.done = event
        self.steps.sort(key=lambda e: e.get("step") or 0)

        # corrected outcome per step, parallel to self.steps
        self.corrected: list[str] = []
        self.flipped: list[dict] = []
        for step in self.steps:
            outcome = step.get("outcome")
            if outcome == "advanced" and messages_report_failure(step.get("lean_messages")):
                self.corrected.append("error")
                self.flipped.append(step)
            else:
                self.corrected.append(outcome)

    @property
    def result(self):
        return self.done.get("result") if self.done else None

    @property
    def proved(self) -> bool:
        return self.result == "PROVED"


def resolve_dirs(args) -> list[Path]:
    if args.all_runs:
        base = Path(args.log_base)
        if not base.is_dir():
            sys.exit("log base directory not found: %s" % base)
        dirs = sorted(d for d in base.iterdir() if d.is_dir())
        if not dirs:
            sys.exit("no run directories under %s" % base)
        return dirs
    target = args.logs_dir or args.positional
    if not target:
        sys.exit("give a log directory, --logs-dir <dir>, or --all-runs")
    d = Path(target)
    if not d.is_dir():
        sys.exit("not a directory: %s" % d)
    return [d]


def load_proofs(dirs: list[Path]) -> list[Proof]:
    proofs = []
    for d in dirs:
        for f in sorted(d.glob("proof_*.jsonl")):
            proofs.append(Proof(f))
    return proofs


# ---------------------------------------------------------------------------
# RQ4 metrics, computed over a chosen outcome view
# ---------------------------------------------------------------------------

def outcomes_of(proof: Proof, corrected: bool) -> list:
    return proof.corrected if corrected else [s.get("outcome") for s in proof.steps]


def advanced_rates(proofs: list[Proof], corrected: bool, max_step: int = MAX_STEP):
    adv = [0] * (max_step + 1)
    tot = [0] * (max_step + 1)
    for p in proofs:
        outs = outcomes_of(p, corrected)
        for step, outcome in zip(p.steps, outs):
            n = step.get("step")
            if not isinstance(n, int) or not (1 <= n <= max_step):
                continue
            tot[n] += 1
            if outcome == "advanced":
                adv[n] += 1
    return [(adv[i] / tot[i] if tot[i] else None, adv[i], tot[i])
            for i in range(1, max_step + 1)]


def repetition(proofs: list[Proof]):
    """Unaffected by reclassification -- reported for completeness."""
    rates, repeats, steps = [], 0, 0
    for p in proofs:
        tactics = [str(s.get("tactic") or "") for s in p.steps]
        tactics = [t for t in tactics if t.strip()]
        if not tactics:
            continue
        seen, r = set(), 0
        for t in tactics:
            if t in seen:
                r += 1
            else:
                seen.add(t)
        rates.append(r / len(tactics))
        repeats += r
        steps += len(tactics)
    mean = statistics.fmean(rates) if rates else 0.0
    std = statistics.stdev(rates) if len(rates) > 1 else 0.0
    return mean, std, repeats, steps, len(rates)


def goal_length_at(proof: Proof, target: int):
    usable = [s for s in proof.steps if isinstance(s.get("goal_before"), str)]
    if not usable:
        return None
    chosen = None
    for step in usable:
        n = step.get("step")
        if isinstance(n, int) and n <= target:
            chosen = step
        elif isinstance(n, int) and n > target:
            break
    if chosen is None:
        return None
    return len(chosen["goal_before"])


def shrinkage(proofs: list[Proof]):
    groups = {
        "PROVED": [p for p in proofs if p.proved],
        "FAILED": [p for p in proofs if p.result is not None and not p.proved],
    }
    out = {}
    for label, group in groups.items():
        measurable = [p for p in group if goal_length_at(p, SHRINK_PROBES[0]) is not None]
        row = {}
        for probe in SHRINK_PROBES:
            vals = [v for v in (goal_length_at(p, probe) for p in measurable) if v is not None]
            row[probe] = statistics.fmean(vals) if vals else None
        out[label] = (len(measurable), row)
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(proofs: list[Proof], dirs: list[Path]) -> str:
    L = []
    L.append("=" * 76)
    L.append("RQ4 RECLASSIFIED -- advanced-despite-error corrected (read-only)")
    L.append("=" * 76)
    for d in dirs:
        L.append("  %s" % d)
    total_steps = sum(len(p.steps) for p in proofs)
    flipped = [(p, s) for p in proofs for s in p.flipped]
    L.append("  proofs: %d   step events: %d" % (len(proofs), total_steps))
    L.append("  keywords: %s" % ", ".join(KEYWORDS))
    L.append("")

    # ---- what changed ----
    L.append("RECLASSIFIED STEPS  (advanced -> error)")
    L.append("=" * 76)
    if not flipped:
        L.append("  None. This run is unaffected.")
    else:
        L.append("  %d of %d step events (%.1f%% of all steps)"
                 % (len(flipped), total_steps, 100.0 * len(flipped) / total_steps))
        L.append("")
        L.append("  %-20s %5s  %s" % ("file", "step", "lean message"))
        L.append("  " + "-" * 72)
        for p, s in flipped:
            msg = " ".join((s.get("lean_messages") or [""])[0].split())[:44]
            L.append("  %-20s %5s  %s" % (p.path.name, s.get("step"), msg))
    L.append("")

    # ---- repetition (unchanged by definition) ----
    mean, std, repeats, steps, n = repetition(proofs)
    L.append("REPETITION RATE  (unaffected -- keys on tactic text, not outcome)")
    L.append("=" * 76)
    L.append("  %.3f +/- %.3f over %d proofs; pooled %d/%d = %.1f%%"
             % (mean, std, n, repeats, steps, 100.0 * repeats / steps if steps else 0))
    L.append("")

    # ---- advanced-rate, both views ----
    orig = advanced_rates(proofs, corrected=False)
    corr = advanced_rates(proofs, corrected=True)
    L.append("ADVANCED-RATE BY STEP  --  original vs corrected")
    L.append("=" * 76)
    L.append("  step |      original      |     corrected      |  delta")
    L.append("  " + "-" * 62)
    for i, ((o_r, o_a, o_t), (c_r, c_a, c_t)) in enumerate(zip(orig, corr), start=1):
        if not o_t:
            L.append("  %4d |         --         |         --         |    --" % i)
            continue
        delta = (c_r - o_r) * 100
        L.append("  %4d | %4d/%-4d = %5.1f%%  | %4d/%-4d = %5.1f%%  | %+6.1f pp"
                 % (i, o_a, o_t, o_r * 100, c_a, c_t, c_r * 100, delta))

    o_adv = sum(a for _, a, _ in orig)
    c_adv = sum(a for _, a, _ in corr)
    o_tot = sum(t for _, _, t in orig)
    L.append("  " + "-" * 62)
    L.append("  all  | %4d/%-4d = %5.1f%%  | %4d/%-4d = %5.1f%%  | %+6.1f pp"
             % (o_adv, o_tot, 100.0 * o_adv / o_tot if o_tot else 0,
                c_adv, o_tot, 100.0 * c_adv / o_tot if o_tot else 0,
                (100.0 * c_adv / o_tot - 100.0 * o_adv / o_tot) if o_tot else 0))
    if o_adv:
        L.append("")
        L.append("  %d of %d steps originally called 'advanced' were failures (%.1f%%)."
                 % (len(flipped), o_adv, 100.0 * len(flipped) / o_adv))
    L.append("")

    # ---- goal shrinkage ----
    L.append("GOAL SHRINKAGE  (unaffected -- keys on goal_before, not outcome)")
    L.append("=" * 76)
    sh = shrinkage(proofs)
    header = "  %-8s %5s" % ("group", "n")
    for probe in SHRINK_PROBES:
        header += " %9s" % ("step %d" % probe)
    L.append(header)
    L.append("  " + "-" * (14 + 10 * len(SHRINK_PROBES)))
    for label in ("PROVED", "FAILED"):
        n_g, row = sh[label]
        line = "  %-8s %5d" % (label, n_g)
        for probe in SHRINK_PROBES:
            v = row[probe]
            line += " %9s" % ("%.0f" % v if v is not None else "--")
        L.append(line)
    L.append("")
    L.append("NOTE: read-only. No JSONL file was modified.")
    return "\n".join(L)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-score RQ4 with advanced-despite-error steps corrected. "
                    "Read-only: no JSONL file is modified.")
    parser.add_argument("positional", nargs="?", help="log directory")
    parser.add_argument("--logs-dir", help="log directory (same as positional)")
    parser.add_argument("--all-runs", action="store_true",
                        help="merge every run directory under --log-base")
    parser.add_argument("--log-base", default=DEFAULT_LOG_BASE)
    parser.add_argument("--out", help="write the report here instead of stdout")
    args = parser.parse_args(argv)

    dirs = resolve_dirs(args)
    proofs = load_proofs(dirs)
    if not proofs:
        sys.exit("no proof_*.jsonl files found in: %s" % ", ".join(str(d) for d in dirs))

    report = build_report(proofs, dirs)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print("Report written to %s" % args.out)
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
