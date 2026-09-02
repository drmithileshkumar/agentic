"""
ProofLogger -- structured JSONL logging for every agent <-> Lean exchange.

Schema (one JSON object per line):
    proof_id        uuid4 -- unique per theorem attempt
    theorem         the theorem statement string
    event           "start" | "step" | "done" | "error"
    step            int (only on "step" events)
    tactic          str (only on "step" events)
    outcome         "proved" | "advanced" | "no_progress" | "error" | "rejected"
    lean_messages   list[str] -- raw messages from Lean (errors, warnings, etc.)
    goal_before     str -- goal state before the tactic
    goal_after      str | null -- goal state after (null if unchanged or done)
    llm_calls       int -- LLM API calls made in this step
    lean_calls      int -- Lean REPL calls made in this step
    result          "PROVED" | "FAILED_BUDGET_EXHAUSTED" | "FAILED_ELABORATION" (only on "done")
    total_steps     int (only on "done")
    tactic_outcomes dict[outcome -> count] (only on "done")
    timestamp       ISO-8601 UTC
"""

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ProofLogger:
    """
    Writes every agent <-> Lean message to a JSONL file and maintains
    in-memory stats for the current proof.

    Usage:
        logger = ProofLogger("logs/run_001.jsonl")
        proof_id = logger.start(theorem)
        logger.step(proof_id, theorem, step=1, tactic="rw [...]", ...)
        logger.done(proof_id, theorem, result="PROVED", ...)
    """

    def __init__(self, log_path: str, echo: bool = True):
        """
        Args:
            log_path: path to the JSONL file (created/appended).
            echo: if True, print each event to stdout as well.
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo

        # per-proof accumulators, keyed by proof_id
        self._tactic_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._tactics_used:    dict[str, list[str]]      = defaultdict(list)
        self._llm_calls:       dict[str, int]            = defaultdict(int)
        self._lean_calls:      dict[str, int]            = defaultdict(int)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def new_proof_id(self) -> str:
        return str(uuid.uuid4())

    def start(self, proof_id: str, theorem: str, goal: str) -> None:
        self._write({
            "proof_id": proof_id,
            "theorem":  theorem,
            "event":    "start",
            "goal":     goal,
        })

    def step(
        self,
        proof_id:      str,
        theorem:       str,
        step:          int,
        tactic:        str,
        outcome:       str,   # "proved" | "advanced" | "no_progress" | "error" | "rejected"
        goal_before:   str,
        goal_after:    Optional[str],
        lean_messages: list[str],
        llm_calls:     int = 1,
        lean_calls:    int = 1,
    ) -> None:
        """Log one proof step and accumulate stats."""
        self._tactic_outcomes[proof_id][outcome] += 1
        self._tactics_used[proof_id].append(tactic)
        self._llm_calls[proof_id]  += llm_calls
        self._lean_calls[proof_id] += lean_calls

        self._write({
            "proof_id":      proof_id,
            "theorem":       theorem,
            "event":         "step",
            "step":          step,
            "tactic":        tactic,
            "outcome":       outcome,
            "goal_before":   goal_before,
            "goal_after":    goal_after,
            "lean_messages": lean_messages,
            "llm_calls":     llm_calls,
            "lean_calls":    lean_calls,
        })

    def done(
        self,
        proof_id:    str,
        theorem:     str,
        result:      str,   # "PROVED" | "FAILED_BUDGET_EXHAUSTED" | "FAILED_ELABORATION"
        total_steps: int,
    ) -> None:
        """Log the final summary event for a proof."""
        self._write({
            "proof_id":       proof_id,
            "theorem":        theorem,
            "event":          "done",
            "result":         result,
            "total_steps":    total_steps,
            "total_llm_calls":  self._llm_calls[proof_id],
            "total_lean_calls": self._lean_calls[proof_id],
            "tactic_outcomes":  dict(self._tactic_outcomes[proof_id]),
            "tactics_used":     self._tactics_used[proof_id],
            # normalised: lean calls per step
            "lean_calls_per_step": (
                self._lean_calls[proof_id] / total_steps if total_steps else 0
            ),
            "llm_calls_per_step": (
                self._llm_calls[proof_id] / total_steps if total_steps else 0
            ),
        })

    def error(self, proof_id: str, theorem: str, detail: str, raw: object = None) -> None:
        self._write({
            "proof_id": proof_id,
            "theorem":  theorem,
            "event":    "error",
            "detail":   detail,
            "raw":      raw,
        })

    # ------------------------------------------------------------------
    # stats helpers (can be called any time after steps are logged)
    # ------------------------------------------------------------------

    def tactic_outcomes(self, proof_id: str) -> dict[str, int]:
        return dict(self._tactic_outcomes[proof_id])

    def llm_calls_total(self, proof_id: str) -> int:
        return self._llm_calls[proof_id]

    def lean_calls_total(self, proof_id: str) -> int:
        return self._lean_calls[proof_id]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _write(self, event: dict) -> None:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(event)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.echo:
            print(line)
