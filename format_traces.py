"""
format_traces.py -- render JSONL proof logs as readable proof traces.

Turns the harness's machine-oriented event stream into a document a Lean 4
reader can follow: the theorem, the initial goal, then each tactic with what
Lean said back, narrated in the style of a proof-assistant session.

Usage
-----
    python format_traces.py logs\\20260913_011157
    python format_traces.py logs\\20260913_011157 --out traces.txt
    python format_traces.py logs\\20260913_011157 --proved-only
    python format_traces.py logs\\20260913_011157 --proof proof_0003.jsonl

Stdlib only. Malformed files are skipped with a warning on stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

RULE_HEAVY = "━" * 62      # ━ theorem header
RULE_LIGHT = "─" * 61      # ─ between steps
RULE_DOUBLE = "═" * 62     # ═ run summary
TURNSTILE = "⊢"            # ⊢

THEOREM_NAME_RE = re.compile(r"\btheorem\s+(\w+)")

# Step label shown in brackets after "Step N". A step that reached Lean and was
# accepted is just a tactic; the others are worth naming.
STEP_LABELS = {
    "proved":             "tactic",
    "advanced":           "tactic",
    "no_progress":        "tactic",
    "error":              "error",
    "rejected":           "rejected",
    "connectivity_error": "connectivity",
}

TOP_TACTICS = 5


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def read_events(path: Path) -> list[dict]:
    """Parse one JSONL file. Raises ValueError if nothing usable is in it."""
    events = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue                      # a truncated final line costs one event
        if isinstance(event, dict):
            events.append(event)
    if not events:
        raise ValueError("no JSON objects found")
    return events


class Proof:
    def __init__(self, path: Path):
        self.path = path
        self.start = None
        self.done = None
        self.error = None
        self.steps: list[dict] = []
        for event in read_events(path):
            kind = event.get("event")
            if kind == "start":
                self.start = event
            elif kind == "step":
                self.steps.append(event)
            elif kind == "done":
                self.done = event
            elif kind == "error" and self.error is None:
                self.error = event
        self.steps.sort(key=lambda e: e.get("step") or 0)

    @property
    def theorem_raw(self) -> str:
        for src in (self.start, self.done, self.error):
            if src and src.get("theorem"):
                return str(src["theorem"])
        for step in self.steps:
            if step.get("theorem"):
                return str(step["theorem"])
        return ""

    @property
    def name(self) -> str:
        match = THEOREM_NAME_RE.search(self.theorem_raw)
        return match.group(1) if match else self.path.stem

    @property
    def statement(self) -> str:
        """The signature on one line, without the import header or sorry body."""
        body = self.theorem_raw.replace("import Mathlib", "")
        body = " ".join(body.split())
        return body.strip()

    @property
    def result(self) -> str:
        if self.done:
            return str(self.done.get("result") or "UNKNOWN")
        return "INCOMPLETE"

    @property
    def proved(self) -> bool:
        return self.result == "PROVED"

    @property
    def total_steps(self) -> int:
        if self.done and isinstance(self.done.get("total_steps"), int):
            return self.done["total_steps"]
        return len(self.steps)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def goal_block(goal, indent: str = "    ") -> list[str]:
    """Indent a goal state, putting a turnstile on the conclusion line."""
    if not isinstance(goal, str) or not goal.strip():
        return [indent + "(goal not recorded)"]
    lines = []
    for raw in goal.strip().splitlines():
        line = raw.rstrip()
        if not line:
            continue
        # Lean already writes the turnstile; don't add a second one.
        lines.append(indent + line)
    return lines or [indent + "(goal not recorded)"]


def message_block(message, indent: str = "    ") -> list[str]:
    if not isinstance(message, str) or not message.strip():
        return []
    return [indent + line.rstrip()
            for line in message.strip().splitlines() if line.strip()]


def outcome_headline(proof: Proof) -> str:
    n = proof.total_steps
    plural = "step" if n == 1 else "steps"
    if proof.proved:
        return "PROVED in %d %s" % (n, plural)
    if proof.result == "INCOMPLETE":
        return "INCOMPLETE — run cut off after %d %s" % (n, plural)
    label = proof.result[len("FAILED_"):] if proof.result.startswith("FAILED_") else proof.result
    return "FAILED — %s after %d %s" % (label, n, plural)


def render_proof(proof: Proof) -> list[str]:
    out = [RULE_HEAVY,
           "Theorem  : %s" % proof.name,
           "Statement: %s" % (proof.statement or "(not recorded)"),
           "Outcome  : %s" % outcome_headline(proof),
           RULE_HEAVY,
           ""]

    initial = proof.start.get("goal") if proof.start else None
    if initial is None and proof.steps:
        initial = proof.steps[0].get("goal_before")
    out.append("Initial goal:")
    out.extend(goal_block(initial, indent="  " + TURNSTILE + "  ")
               if isinstance(initial, str) and TURNSTILE not in (initial or "")
               else goal_block(initial))
    out.append("")

    if not proof.steps:
        detail = (proof.error or {}).get("detail")
        out.append("  No tactic was attempted.")
        if detail:
            out.append("  Reason:")
            out.extend(message_block(detail, indent="    "))
        out.append("")
        return out

    for step in proof.steps:
        outcome = str(step.get("outcome") or "unknown")
        label = STEP_LABELS.get(outcome, outcome)
        tactic = step.get("tactic")
        messages = step.get("lean_messages") or []

        out.append("  Step %s  [%s]" % (step.get("step"), label))
        if isinstance(tactic, str) and tactic.strip():
            out.extend("    " + line.rstrip()
                       for line in tactic.strip().splitlines() if line.strip())
        else:
            out.append("    (no tactic)")
        out.append("  Result: %s" % outcome)

        if outcome == "proved":
            out.append("  Proof complete.")
            out.append("  " + RULE_LIGHT)
            break

        if outcome == "error":
            if messages:
                out.append("  Lean says:")
                out.extend(message_block(messages[0]))
            out.append("  Remaining goal:")
            out.extend(goal_block(step.get("goal_before")))

        elif outcome == "advanced":
            out.append("  Goal updated:")
            out.extend(goal_block(step.get("goal_after") or step.get("goal_before")))

        elif outcome == "no_progress":
            out.append("  No change.")

        elif outcome == "rejected":
            out.append("  Tactic rejected (empty or malformed).")
            if messages:
                out.extend(message_block(messages[0]))

        elif outcome == "connectivity_error":
            out.append("  Endpoint unreachable; retried after a backoff.")
            if messages:
                out.extend(message_block(messages[0]))

        out.append("  " + RULE_LIGHT)

    # A run that died on the LLM side did not finish reasoning about the goal.
    if proof.result in ("FAILED_LLM_ERROR", "FAILED_CONNECTIVITY_OUTAGE"):
        out.append("  Run aborted — LLM unavailable after step %d." % proof.total_steps)
        out.append("")
    elif proof.result == "FAILED_BUDGET_EXHAUSTED":
        out.append("  Step budget exhausted; goal still open.")
        out.append("")
    else:
        out.append("")
    return out


def tactic_head(tactic: object) -> str | None:
    """Leading identifier of a tactic, for aggregate counting."""
    if not isinstance(tactic, str) or not tactic.strip():
        return None
    match = re.match(r"[A-Za-z_][A-Za-z0-9_']*", tactic.strip())
    return match.group(0) if match else None


def render_summary(proofs: list[Proof], logs_dir: Path) -> list[str]:
    total = len(proofs)
    proved = sum(1 for p in proofs if p.proved)
    pct = (100.0 * proved / total) if total else 0.0

    failures = Counter(p.result for p in proofs if not p.proved)
    fail_text = ", ".join("%s ×%d" % (code, n)
                          for code, n in failures.most_common()) or "none"

    heads: Counter = Counter()
    for p in proofs:
        for step in p.steps:
            head = tactic_head(step.get("tactic"))
            if head:
                heads[head] += 1
    tactic_text = ", ".join("%s ×%d" % (h, n)
                            for h, n in heads.most_common(TOP_TACTICS)) or "none"
    if len(heads) > TOP_TACTICS:
        tactic_text += ", ..."

    return [RULE_DOUBLE,
            "Run summary  : %s" % logs_dir,
            "Proved       : %d / %d  (%.1f%%)" % (proved, total, pct),
            "Failure modes: %s" % fail_text,
            "Tactics used : %s" % tactic_text,
            RULE_DOUBLE]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render JSONL proof logs as readable proof traces.")
    parser.add_argument("logs_dir", help="run directory holding proof_*.jsonl")
    parser.add_argument("--out", help="write the document here instead of stdout")
    parser.add_argument("--proved-only", action="store_true",
                        help="skip proofs that did not close")
    parser.add_argument("--proof", help="render a single file, e.g. proof_0003.jsonl")
    args = parser.parse_args(argv)

    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_dir():
        sys.exit("not a directory: %s" % logs_dir)

    if args.proof:
        candidate = logs_dir / args.proof
        if not candidate.is_file():
            sys.exit("no such proof file: %s" % candidate)
        files = [candidate]
    else:
        files = sorted(logs_dir.glob("proof_*.jsonl"))
    if not files:
        sys.exit("no proof_*.jsonl files in %s" % logs_dir)

    proofs = []
    for path in files:
        try:
            proofs.append(Proof(path))
        except (OSError, ValueError) as exc:
            print("warning: skipping %s (%s)" % (path.name, exc), file=sys.stderr)

    if not proofs:
        sys.exit("no readable proof files in %s" % logs_dir)

    shown = [p for p in proofs if p.proved] if args.proved_only else proofs
    if args.proved_only and not shown:
        print("warning: --proved-only and no proof closed; showing summary only",
              file=sys.stderr)

    lines: list[str] = []
    for proof in shown:
        lines.extend(render_proof(proof))
    # The summary describes the whole run, not just what was displayed.
    lines.extend(render_summary(proofs, logs_dir))
    document = "\n".join(lines) + "\n"

    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        print("Wrote %s (%d proof(s))" % (args.out, len(shown)))
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
