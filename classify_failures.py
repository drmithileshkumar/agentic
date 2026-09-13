"""
classify_failures.py -- failure mode taxonomy for failed proofs (RQ2).

Reads the proof_XXXX.jsonl files in a run directory and classifies every proof
whose done event carries a result other than "PROVED".

Classification is first-match-wins, in this order:
    1. NO_PROGRESS_LOOP        last 3 steps all outcome == "no_progress"
    2. ERROR_LOOP              identical lean_messages[0] across the last 3
                               steps that produced any message
    3. HALLUCINATED_LEMMA      "unknown identifier" in a step's messages while
                               the tactic names a capitalised non-stdlib symbol
    4. SYNTAX_ERROR_LOOP       3+ steps whose messages mention "expected" or
                               "parse error"
    5. BUDGET_EXHAUSTION_MIXED none of the above

Usage
-----
    python classify_failures.py --logs-dir C:\\lean\\logs\\20260906_191021
    python classify_failures.py --all-runs
    python classify_failures.py --all-runs --out failures.txt

Stdlib only -- no pandas, no scipy, no matplotlib.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

DEFAULT_LOG_BASE = r"C:\lean\logs"

# Capitalised names that are Lean/Mathlib stdlib, not model inventions. A tactic
# naming only these is not evidence of a hallucinated lemma.
STDLIB_NAMES = frozenset({
    "Nat", "Int", "Real", "List", "Set", "Finset", "Bool", "Prop", "Type", "Sort",
})

# Leading capitalised identifier of a dotted path: Group.inv_mul -> Group.
_CAP_IDENT_RE = re.compile(r"[A-Z][A-Za-z0-9_']*")

MODES = (
    "NO_PROGRESS_LOOP",
    "ERROR_LOOP",
    "HALLUCINATED_LEMMA",
    "SYNTAX_ERROR_LOOP",
    "BUDGET_EXHAUSTION_MIXED",
)


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
    def proof_id(self) -> str:
        for src in (self.done, self.start):
            if src and src.get("proof_id"):
                return str(src["proof_id"])
        return self.path.stem

    @property
    def theorem(self) -> str:
        for src in (self.start, self.done):
            if src and src.get("theorem"):
                return str(src["theorem"])
        return ""

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


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _messages(step: dict) -> list[str]:
    """lean_messages as a list of strings, tolerating null/missing/odd shapes."""
    raw = step.get("lean_messages")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return [str(raw)]
    return [m if isinstance(m, str) else str(m) for m in raw if m is not None]


def _first_message(step: dict) -> str | None:
    msgs = _messages(step)
    return msgs[0] if msgs else None


def _joined(step: dict) -> str:
    return " ".join(_messages(step)).lower()


def names_a_hallucinated_symbol(tactic: object) -> list[str]:
    """Capitalised identifiers in *tactic* that are not known stdlib roots."""
    if not isinstance(tactic, str):
        return []
    found = []
    for match in _CAP_IDENT_RE.findall(tactic):
        if match not in STDLIB_NAMES and match not in found:
            found.append(match)
    return found


def classify(proof: Proof) -> tuple[str, list[dict]]:
    """Return (mode, evidence steps) for a failed *proof*."""
    steps = proof.steps

    # 1. NO_PROGRESS_LOOP -- the model is spinning without moving the goal.
    if len(steps) >= 3:
        tail = steps[-3:]
        if all(s.get("outcome") == "no_progress" for s in tail):
            return "NO_PROGRESS_LOOP", tail

    # 2. ERROR_LOOP -- Lean keeps saying exactly the same thing.
    with_msgs = [s for s in steps if _first_message(s)]
    if len(with_msgs) >= 3:
        tail = with_msgs[-3:]
        first = _first_message(tail[0])
        if all(_first_message(s) == first for s in tail[1:]):
            return "ERROR_LOOP", tail

    # 3. HALLUCINATED_LEMMA -- an unknown identifier the model appears to have
    #    invented, as opposed to a stdlib name used wrongly.
    hallucinated = [s for s in steps
                    if "unknown identifier" in _joined(s)
                    and names_a_hallucinated_symbol(s.get("tactic"))]
    if hallucinated:
        return "HALLUCINATED_LEMMA", hallucinated[:3]

    # 4. SYNTAX_ERROR_LOOP -- repeatedly emitting text Lean cannot parse.
    syntax = [s for s in steps
              if "expected" in _joined(s) or "parse error" in _joined(s)]
    if len(syntax) >= 3:
        return "SYNTAX_ERROR_LOOP", syntax[:3]

    # 5. Everything else: the budget simply ran out.
    return "BUDGET_EXHAUSTION_MIXED", steps[-3:]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _one_line_theorem(theorem: str) -> str:
    body = theorem.replace("import Mathlib", "").strip()
    return " ".join(body.split())


def _render_evidence(steps: list[dict], indent: str = "      ") -> list[str]:
    lines = []
    for step in steps:
        number = step.get("step")
        tactic = " ".join(str(step.get("tactic") or "").split())
        if len(tactic) > 64:
            tactic = tactic[:63] + "\u2026"
        lines.append("%sstep %-3s %-11s %r"
                     % (indent, number, step.get("outcome"), tactic))
        msg = _first_message(step)
        if msg:
            flat = " ".join(msg.split())
            if len(flat) > 84:
                flat = flat[:83] + "\u2026"
            lines.append("%s          lean: %s" % (indent, flat))
    return lines


MODE_DESCRIPTIONS = {
    "NO_PROGRESS_LOOP":
        "Lean accepted the tactic but the goal never changed -- the agent spun in place.",
    "ERROR_LOOP":
        "Lean returned the identical error three steps running -- the agent did not adapt.",
    "HALLUCINATED_LEMMA":
        "The tactic named a capitalised symbol Lean does not know -- an invented lemma.",
    "SYNTAX_ERROR_LOOP":
        "Three or more steps emitted text Lean could not parse.",
    "BUDGET_EXHAUSTION_MIXED":
        "No single pathology dominated; the step budget simply ran out.",
}


def bucket_failures(proofs: list[Proof]) -> "OrderedDict[str, list]":
    """Group every failed proof under its mode, preserving MODES order."""
    buckets: "OrderedDict[str, list]" = OrderedDict((m, []) for m in MODES)
    for proof in proofs:
        if proof.result is None or proof.proved:
            continue
        mode, evidence = classify(proof)
        buckets[mode].append((proof, evidence))
    return buckets


def _step_json(step: dict) -> dict:
    return {
        "step": step.get("step"),
        "tactic": step.get("tactic"),
        "outcome": step.get("outcome"),
        "message": _first_message(step),
    }


def build_json(proofs: list[Proof]) -> dict:
    """Machine-readable form of the same taxonomy the report renders."""
    buckets = bucket_failures(proofs)
    total = sum(len(v) for v in buckets.values())

    modes = {}
    for mode in MODES:
        entries = buckets[mode]
        example = None
        if entries:
            proof, evidence = entries[0]
            example = {
                "proof_id": proof.proof_id,
                "theorem": _one_line_theorem(proof.theorem),
                "steps": [_step_json(s) for s in evidence],
            }
        modes[mode] = {
            "count": len(entries),
            "pct": round(100.0 * len(entries) / total, 4) if total else 0.0,
            "description": MODE_DESCRIPTIONS[mode],
            "example": example,
        }

    return {"total_failed": total, "modes": modes}


def build_report(proofs: list[Proof], dirs: list[Path]) -> str:
    finished = [p for p in proofs if p.result is not None]
    incomplete = [p for p in proofs if p.result is None]
    failed = [p for p in finished if not p.proved]

    lines = [
        "=" * 72,
        "FAILURE MODE TAXONOMY  (RQ2)",
        "=" * 72,
        "Run directories : %d" % len(dirs),
    ]
    for d in dirs:
        lines.append("  %s" % d)
    lines.append("Proof files     : %d" % len(proofs))
    lines.append("Completed       : %d  (proved %d, failed %d)"
                 % (len(finished), len(finished) - len(failed), len(failed)))
    if incomplete:
        lines.append("Incomplete      : %d  (no done event -- run cut off; excluded)"
                     % len(incomplete))
    lines.append("")

    if not failed:
        lines.append("No failed proofs in this data -- nothing to classify.")
        lines.append("")
        return "\n".join(lines)

    buckets = bucket_failures(proofs)
    counts = Counter({m: len(v) for m, v in buckets.items()})
    lines.append("DISTRIBUTION")
    lines.append("=" * 72)
    lines.append("  %-26s %6s %8s" % ("mode", "count", "share"))
    lines.append("  " + "-" * 44)
    for mode in MODES:
        n = counts[mode]
        lines.append("  %-26s %6d %7.1f%%"
                     % (mode, n, 100.0 * n / len(failed) if failed else 0.0))
    lines.append("  " + "-" * 44)
    lines.append("  %-26s %6d %7.1f%%" % ("TOTAL", len(failed), 100.0))
    lines.append("")

    lines.append("EXAMPLES  (one per mode, with the steps that triggered it)")
    lines.append("=" * 72)
    for mode in MODES:
        entries = buckets[mode]
        lines.append("")
        lines.append("  %s  -- %d proof(s)" % (mode, len(entries)))
        lines.append("  " + "-" * 68)
        if not entries:
            lines.append("      (none)")
            continue
        proof, evidence = entries[0]
        lines.append("      proof_id : %s" % proof.proof_id)
        lines.append("      file     : %s" % proof.path.name)
        lines.append("      result   : %s" % proof.result)
        lines.append("      theorem  : %s" % _one_line_theorem(proof.theorem))
        if mode == "HALLUCINATED_LEMMA" and evidence:
            names = names_a_hallucinated_symbol(evidence[0].get("tactic"))
            if names:
                lines.append("      invented : %s" % ", ".join(names))
        lines.append("      evidence :")
        lines.extend(_render_evidence(evidence) or ["      (no step events)"])
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify failed proofs into failure modes (RQ2).")
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
