#!/usr/bin/env python3
"""Render each proof log as a readable Markdown chapter.

`format_traces.py` produces a terminal transcript for someone who knows Lean.
This produces something different: a narrated document for a reader who knows
mathematics but not Lean syntax. Outcome codes become sentences, goal states sit
in code blocks, and each step says what the agent was looking at, what it tried,
and what came back.

    python render_proof_docs.py                     # every run under C:\\lean\\logs
    python render_proof_docs.py --logs-dir logs\\20260913_165146
    python render_proof_docs.py --dry-run           # list what would be written

Writes `proof_NNNN.md` beside each `proof_NNNN.jsonl`, plus an `index.md` per
run directory. Re-running overwrites; nothing else is touched.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_ROOT = HERE / "logs"
CANDIDATES = HERE / "MyMathlibProject" / "problems" / "candidates.jsonl"

THEOREM_NAME_RE = re.compile(r"\btheorem\s+(\w+)")

# Outcome codes -> what actually happened, for a reader who does not know Lean.
OUTCOME_PROSE = {
    "proved": "The proof is complete.",
    "advanced": "Lean accepted the tactic, and the goal changed.",
    "no_progress": "Lean accepted the tactic, but the goal did not change.",
    "error": "Lean rejected the tactic.",
    "rejected": "The tactic was malformed and never reached Lean.",
    "connectivity_error": "The language model could not be reached.",
}

OUTCOME_HEADING = {
    "proved": "proof complete",
    "advanced": "progress",
    "no_progress": "no change",
    "error": "rejected by Lean",
    "rejected": "malformed tactic",
    "connectivity_error": "model unreachable",
}

RESULT_PROSE = {
    "PROVED": "The agent proved the theorem.",
    "FAILED_BUDGET_EXHAUSTED":
        "The agent ran out of its step budget with the goal still open.",
    "FAILED_ELABORATION":
        "Lean could not make sense of the theorem statement itself, so no "
        "proof was ever attempted.",
    "FAILED_LLM_ERROR":
        "The language model stopped returning usable answers, so the attempt "
        "was abandoned. This is an infrastructure failure, not a mathematical one.",
    "FAILED_CONNECTIVITY_OUTAGE":
        "The model provider was unreachable for long enough that the attempt "
        "was abandoned. This is an infrastructure failure, not a mathematical one.",
    "INCOMPLETE":
        "The run was interrupted before this proof finished.",
}

FAILURE_MODE_PROSE = {
    "NO_PROGRESS_LOOP":
        "**Stuck in place.** The last few tactics were accepted by Lean but "
        "left the goal exactly as it was.",
    "ERROR_LOOP":
        "**Stuck on the same error.** Lean returned an identical complaint "
        "several steps running, and the agent did not change course.",
    "HALLUCINATED_LEMMA":
        "**Invented a lemma.** The agent referred to a named result that does "
        "not exist in the library.",
    "SYNTAX_ERROR_LOOP":
        "**Could not write valid Lean.** Several steps produced text Lean was "
        "unable to parse at all.",
    "BUDGET_EXHAUSTION_MIXED":
        "**No single cause.** The agent made scattered progress and simply ran "
        "out of steps.",
}


# ---------------------------------------------------------------------------
# Loading
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
        m = THEOREM_NAME_RE.search(self.theorem_raw)
        return m.group(1) if m else self.path.stem

    @property
    def statement(self) -> str:
        body = self.theorem_raw.replace("import Mathlib", "").strip()
        return re.sub(r"\s*:=\s*by\s+sorry\s*$", "", body).strip()

    @property
    def result(self) -> str:
        return str(self.done.get("result")) if self.done else "INCOMPLETE"

    @property
    def proved(self) -> bool:
        return self.result == "PROVED"

    @property
    def total_steps(self) -> int:
        if self.done and isinstance(self.done.get("total_steps"), int):
            return self.done["total_steps"]
        return len(self.steps)


def load_catalogue() -> dict:
    """theorem name -> {domain, tier} from the problem manifest, if present."""
    out = {}
    if not CANDIDATES.exists():
        return out
    for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("name"):
            out[r["name"]] = {"domain": r.get("domain", ""),
                              "tier": r.get("tier", "")}
    return out


def load_classifier():
    """classify_failures.classify, or None if that script is unavailable."""
    try:
        sys.path.insert(0, str(HERE))
        from classify_failures import classify  # noqa: E402
        return classify
    except Exception:                                   # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Rendering one proof
# ---------------------------------------------------------------------------

def code_block(text, lang: str = "lean") -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return ["```", "(not recorded)", "```"]
    body = [l.rstrip() for l in text.strip().splitlines() if l.strip()]
    return ["```" + lang] + body + ["```"]


def retry_sentence(step: dict) -> str | None:
    """Plain-English note about how many completions this step needed."""
    attempts = step.get("retry_attempts")
    if not isinstance(attempts, list) or len(attempts) <= 1:
        return None
    skipped = sum(1 for a in attempts if a.get("reason_skipped"))
    if skipped:
        return ("The model returned %d empty answer%s before this one; the "
                "harness asked again each time." % (skipped, "" if skipped == 1 else "s"))
    return "This step took %d attempts." % len(attempts)


def salvage_sentence(step: dict) -> str | None:
    attempts = step.get("retry_attempts")
    if not isinstance(attempts, list):
        return None
    if any(a.get("salvaged") for a in attempts):
        return ("The provider returned an error rather than a completion, but "
                "the model's answer was recoverable from the error body and was "
                "used.")
    return None


def render_step(step: dict, is_last: bool) -> list[str]:
    outcome = str(step.get("outcome") or "unknown")
    n = step.get("step")
    out = ["## Step %s — %s" % (n, OUTCOME_HEADING.get(outcome, outcome)), ""]

    out.append("The agent was looking at this goal:")
    out.append("")
    out.extend(code_block(step.get("goal_before")))
    out.append("")

    tactic = step.get("tactic")
    if isinstance(tactic, str) and tactic.strip():
        out.append("It proposed:")
        out.append("")
        out.extend(code_block(tactic))
        out.append("")
    else:
        out.append("It produced no tactic at all.")
        out.append("")

    for note in (retry_sentence(step), salvage_sentence(step)):
        if note:
            out.append(note)
            out.append("")

    messages = step.get("lean_messages") or []
    if outcome == "connectivity_error":
        out.append(OUTCOME_PROSE[outcome])
        if messages:
            out.append("")
            out.extend(code_block(messages[0], lang="text"))
        out.append("")
        out.append("No mathematics happened on this step; the harness waited "
                   "and tried again.")
    elif outcome == "proved":
        out.append("Lean accepted it and closed the goal. **" +
                   OUTCOME_PROSE[outcome] + "**")
    elif outcome in ("error", "rejected"):
        out.append(OUTCOME_PROSE.get(outcome, ""))
        if messages:
            out.append("")
            out.append("Lean's response:")
            out.append("")
            out.extend(code_block(messages[0], lang="text"))
            out.append("")
        out.append("The goal is unchanged, so the next step starts from the "
                   "same place.")
    elif outcome == "advanced":
        out.append(OUTCOME_PROSE[outcome])
        after = step.get("goal_after")
        if isinstance(after, str) and after.strip():
            out.append("")
            out.append("The goal became:")
            out.append("")
            out.extend(code_block(after))
        if messages:
            out.append("")
            out.append("Lean also said:")
            out.append("")
            out.extend(code_block(messages[0], lang="text"))
    elif outcome == "no_progress":
        out.append(OUTCOME_PROSE[outcome] +
                   " Whatever the tactic did, it did not move the proof on.")
    else:
        out.append("Outcome recorded as `%s`." % outcome)

    out.append("")
    if not is_last:
        out.append("---")
        out.append("")
    return out


def render_proof(proof: Proof, catalogue: dict, classify) -> str:
    meta = catalogue.get(proof.name, {})
    out = ["# %s" % proof.name, ""]

    bits = []
    if meta.get("domain"):
        bits.append("domain `%s`" % meta["domain"])
    if meta.get("tier"):
        bits.append("tier `%s`" % meta["tier"])
    bits.append("%d step%s" % (proof.total_steps,
                               "" if proof.total_steps == 1 else "s"))
    out.append("*%s* — %s" % (
        "Proved" if proof.proved else proof.result.replace("FAILED_", "").replace("_", " ").title(),
        ", ".join(bits)))
    out.append("")

    out.append("## The theorem")
    out.append("")
    out.extend(code_block(proof.statement or "(statement not recorded)"))
    out.append("")

    initial = proof.start.get("goal") if proof.start else None
    if initial is None and proof.steps:
        initial = proof.steps[0].get("goal_before")
    out.append("Lean turns that into the goal the agent starts from. Everything "
               "above the `⊢` is an assumption it may use; the line after `⊢` is "
               "what it must show.")
    out.append("")
    out.extend(code_block(initial))
    out.append("")

    if not proof.steps:
        out.append("## What happened")
        out.append("")
        out.append("No tactic was ever attempted.")
        detail = (proof.error or {}).get("detail")
        if detail:
            out.append("")
            out.extend(code_block(detail, lang="text"))
        out.append("")
    else:
        for i, step in enumerate(proof.steps):
            out.extend(render_step(step, is_last=(i == len(proof.steps) - 1)))

    # ---- summary ----
    out.append("## Outcome")
    out.append("")
    out.append(RESULT_PROSE.get(proof.result,
                                "Result recorded as `%s`." % proof.result))
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append("| Result | `%s` |" % proof.result)
    out.append("| Steps taken | %d |" % proof.total_steps)
    if proof.done:
        outcomes = proof.done.get("tactic_outcomes") or {}
        if outcomes:
            readable = ", ".join("%s x%d" % (k, v) for k, v in sorted(outcomes.items()))
            out.append("| Step outcomes | %s |" % readable)
        for label, key in (("Model calls", "total_llm_calls"),
                           ("Lean calls", "total_lean_calls")):
            if proof.done.get(key) is not None:
                out.append("| %s | %s |" % (label, proof.done[key]))
    out.append("")

    if not proof.proved and proof.steps and classify is not None:
        try:
            mode, _evidence = classify(proof)
        except Exception:                               # noqa: BLE001
            mode = None
        if mode:
            out.append("### Why it failed")
            out.append("")
            out.append(FAILURE_MODE_PROSE.get(mode, "Classified as `%s`." % mode))
            out.append("")
            out.append("Classified as `%s`." % mode)
            out.append("")

    out.append("---")
    out.append("")
    out.append("*Generated from `%s` by `render_proof_docs.py`.*" % proof.path.name)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def render_index(run_dir: Path, proofs: list[Proof], catalogue: dict) -> str:
    total = len(proofs)
    proved = sum(1 for p in proofs if p.proved)
    out = ["# %s" % run_dir.name, ""]
    out.append("%d proof%s, %d proved." % (total, "" if total == 1 else "s", proved))
    out.append("")
    out.append("| Proof | Theorem | Domain | Tier | Result | Steps |")
    out.append("|---|---|---|---|---|---|")
    for p in proofs:
        meta = catalogue.get(p.name, {})
        doc = p.path.with_suffix(".md").name
        out.append("| [%s](%s) | `%s` | %s | %s | `%s` | %d |" % (
            p.path.stem, doc, p.name,
            meta.get("domain") or "—", meta.get("tier") or "—",
            p.result, p.total_steps))
    out.append("")
    out.append("*Generated by `render_proof_docs.py`.*")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render proof logs as narrated Markdown chapters.")
    parser.add_argument("--logs-dir", help="one run directory (default: every run)")
    parser.add_argument("--log-root", default=str(LOG_ROOT))
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be written, write nothing")
    args = parser.parse_args(argv)

    if args.logs_dir:
        run_dirs = [Path(args.logs_dir)]
    else:
        root = Path(args.log_root)
        if not root.is_dir():
            sys.exit("log root not found: %s" % root)
        run_dirs = sorted(d for d in root.iterdir() if d.is_dir())

    catalogue = load_catalogue()
    classify = load_classifier()
    if classify is None:
        print("note: classify_failures.py unavailable; failure modes omitted",
              file=sys.stderr)

    written = skipped = 0
    for run_dir in run_dirs:
        files = sorted(run_dir.glob("proof_*.jsonl"))
        if not files:
            continue
        proofs = [Proof(f) for f in files]
        print("%s  (%d proof%s)" % (run_dir.name, len(proofs),
                                    "" if len(proofs) == 1 else "s"))
        for proof in proofs:
            target = proof.path.with_suffix(".md")
            if args.dry_run:
                print("   would write %s" % target.name)
            else:
                target.write_text(render_proof(proof, catalogue, classify),
                                  encoding="utf-8")
            written += 1
        index = run_dir / "index.md"
        if args.dry_run:
            print("   would write %s" % index.name)
        else:
            index.write_text(render_index(run_dir, proofs, catalogue),
                             encoding="utf-8")

    print()
    print("%s %d proof document%s across %d run director%s%s"
          % ("would write" if args.dry_run else "wrote",
             written, "" if written == 1 else "s",
             len([d for d in run_dirs if list(d.glob('proof_*.jsonl'))]),
             "y" if len(run_dirs) == 1 else "ies",
             ", plus an index each" if written else ""))
    if skipped:
        print("skipped %d" % skipped)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
