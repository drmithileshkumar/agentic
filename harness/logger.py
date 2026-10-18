"""
ProofLogger -- structured JSONL logging for every agent <-> Lean exchange.

Schema (one JSON object per line):
    proof_id        uuid4 -- unique per theorem attempt
    theorem         the theorem statement string
    event           "start" | "step" | "done" | "error" | "challenge" | "defense"
                    | "variant"
    step            int (only on "step" events)
    tactic          str (only on "step" events)
    outcome         "proved" | "advanced" | "no_progress" | "error" | "rejected"
                    | "connectivity_error"
    lean_messages   list[str] -- raw messages from Lean (errors, warnings, etc.)
    goal_before     str -- goal state before the tactic
    goal_after      str | null -- goal state after (null if unchanged or done)
    llm_calls       int -- LLM API calls made in this step
    lean_calls      int -- Lean REPL calls made in this step
    result          "PROVED" | "FAILED_BUDGET_EXHAUSTED" | "FAILED_ELABORATION"
                    | "FAILED_LLM_ERROR" | "FAILED_CONNECTIVITY_OUTAGE" (only on "done")
    total_steps     int (only on "done")
    tactic_outcomes dict[outcome -> count] (only on "done")
    generation_mode str (only on "done") -- how the proof was produced,
                    e.g. "tactic_by_tactic"
    timestamp       ISO-8601 UTC

Adversarial-robustness events (see log_challenge / log_defense):

    step_index          int -- the proof step that was challenged
    challenge_type      str -- kind of challenge issued
    original_tactic     (only on "challenge") the tactic under challenge
    prompt_sent         (only on "challenge") the full challenge prompt
    model_response      (only on "defense") the model's verbatim reply
    modified_proof_text (only on "defense") the proof text after the model
                        responded, or null if it was left unchanged
    lean_outcome        (only on "defense") "sycophantic" | "robust"
                        | "refused" | "improved" | "error"
    lean_messages       (only on "defense") raw messages from Lean when the
                        (possibly modified) proof was re-checked

Calibration events (see variant_generator.run_variant_experiment):

    base_proof_id       (only on "variant") the proof the variant derives from;
                        proof_id on this event is the variant's own id
    variant_kind        (only on "variant") "wrong_hypothesis" | "wrong_conclusion"
    original_theorem    (only on "variant") the true statement
    variant_theorem     (only on "variant") the altered, false statement

Verbatim-transcript fields -- every word that crossed the boundary, so a log
can be replayed or audited without the run being repeated. All are written on
every event of their kind, as null when the harness could not capture them:

    lean_raw_elaboration  (only on "start") the raw REPL response object
                          returned by lean_interact when the theorem was
                          elaborated, as a dict if it round-trips through JSON
                          and a str() rendering otherwise
    llm_input             (only on "step") {"system": <system prompt>,
                          "user": <full user message, formatted goal+history>}
    llm_raw_response      (only on "step") the string the model returned,
                          before stripping or tactic extraction
    retry_attempts        (only on "step") one dict per API call parsed this
                          step: {"attempt": int, "raw_response": str,
                          "extracted_tactic": str} plus either
                          {"reason_skipped": "empty_tactic"} or
                          {"accepted": true}. raw_response is the completion
                          verbatim, extracted_tactic is what was pulled out of
                          it and actually sent to Lean
    history_passed        (only on "step") the history list handed to the LLM
                          for this step: dicts of step/tactic/outcome and, when
                          Lean said something, message
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

    def start(
        self,
        proof_id: str,
        theorem:  str,
        goal:     str,
        lean_raw_elaboration: object = None,
    ) -> None:
        """Log the initial goal, plus Lean's verbatim elaboration response.

        *lean_raw_elaboration* is whatever lean_interact handed back for the
        Command that elaborated the theorem. The caller passes a JSON-friendly
        view of it; anything that still is not serialisable is stringified by
        _write rather than being allowed to kill the run.
        """
        self._write({
            "proof_id": proof_id,
            "theorem":  theorem,
            "event":    "start",
            "goal":     goal,
            "lean_raw_elaboration": lean_raw_elaboration,
        })

    def step(
        self,
        proof_id:      str,
        theorem:       str,
        step:          int,
        tactic:        str,
        outcome:       str,   # proved|advanced|no_progress|error|rejected|connectivity_error
        goal_before:   str,
        goal_after:    Optional[str],
        lean_messages: list[str],
        llm_calls:     int = 1,
        lean_calls:    int = 1,
        llm_input:        Optional[dict]       = None,
        llm_raw_response: Optional[str]        = None,
        retry_attempts:   Optional[list[dict]] = None,
        history_passed:   Optional[list[dict]] = None,
    ) -> None:
        """Log one proof step and accumulate stats.

        The last four arguments are the verbatim record of the LLM half of the
        exchange (see module docstring). They default to None so a caller that
        cannot capture them -- an LLM call that died before it said anything --
        writes null instead of failing.

        *history_passed* is written as it stands at call time, which is exactly
        the history the LLM saw for this step: the caller appends this step's
        own entry only after logging.
        """
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
            "llm_input":        llm_input,
            "llm_raw_response": llm_raw_response,
            "retry_attempts":   retry_attempts,
            "history_passed":   history_passed,
        })

    def done(
        self,
        proof_id:    str,
        theorem:     str,
        result:      str,   # "PROVED" | "FAILED_BUDGET_EXHAUSTED" | "FAILED_ELABORATION"
        total_steps: int,
        generation_mode: str = "tactic_by_tactic",
    ) -> None:
        """Log the final summary event for a proof.

        *generation_mode* records how the proof was produced (default
        "tactic_by_tactic") so runs using different strategies can be told
        apart in the log.
        """
        self._write({
            "proof_id":       proof_id,
            "theorem":        theorem,
            "event":          "done",
            "result":         result,
            "total_steps":    total_steps,
            "generation_mode": generation_mode,
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
    # adversarial-robustness events
    # ------------------------------------------------------------------

    # "error" is the fifth, for a challenge whose LLM call failed: there is no
    # defense to judge, but the attempt still belongs on the record.
    _LEAN_OUTCOMES = ("sycophantic", "robust", "refused", "improved", "error")

    def log_challenge(
        self,
        proof_id:        str,
        step_index:      int,
        challenge_type:  str,
        original_tactic: str,
        prompt_sent:     str,
    ) -> None:
        """Log a challenge issued against one step of a completed proof.

        *prompt_sent* is the full prompt handed to the model, verbatim, so the
        exchange can be audited without being re-run.
        """
        self._write({
            "proof_id":        proof_id,
            "event":           "challenge",
            "step_index":      step_index,
            "challenge_type":  challenge_type,
            "original_tactic": original_tactic,
            "prompt_sent":     prompt_sent,
        })

    def log_defense(
        self,
        proof_id:            str,
        step_index:          int,
        challenge_type:      str,
        model_response:      str,
        modified_proof_text: Optional[str],
        lean_outcome:        str,   # sycophantic|robust|refused|improved|error
        lean_messages:       list[str],
    ) -> None:
        """Log the model's response to a challenge and how Lean judged it.

        *modified_proof_text* is the proof after the model responded, or None
        if the model left it unchanged. *lean_outcome* must be one of
        "sycophantic", "robust", "refused", "improved" or "error" (the LLM
        call for this challenge failed, so there was nothing to check).
        """
        if lean_outcome not in self._LEAN_OUTCOMES:
            raise ValueError(
                f"lean_outcome must be one of {self._LEAN_OUTCOMES}, got {lean_outcome!r}"
            )
        self._write({
            "proof_id":            proof_id,
            "event":               "defense",
            "step_index":          step_index,
            "challenge_type":      challenge_type,
            "model_response":      model_response,
            "modified_proof_text": modified_proof_text,
            "lean_outcome":        lean_outcome,
            "lean_messages":       lean_messages,
        })

    def log_variant(
        self,
        base_proof_id:    str,
        variant_kind:     str,
        variant_proof_id: str,
        original_theorem: str,
        variant_theorem:  str,
    ) -> None:
        """Tie a deliberately-false variant theorem back to its base proof.

        Written once per variant, before the agent's own start event for
        *variant_proof_id*, so the log can say which true theorem a false
        one was derived from.
        """
        self._write({
            "proof_id":         variant_proof_id,
            "event":            "variant",
            "base_proof_id":    base_proof_id,
            "variant_kind":     variant_kind,
            "original_theorem": original_theorem,
            "variant_theorem":  variant_theorem,
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

    # Fields carrying captured foreign objects (Lean REPL responses, model
    # output). If one of them cannot be serialised the event is still worth
    # writing, so it is dropped to null rather than losing the whole line.
    _BEST_EFFORT_FIELDS = (
        "lean_raw_elaboration",
        "llm_input",
        "llm_raw_response",
        "retry_attempts",
        "history_passed",
        "raw",
        "prompt_sent",
        "model_response",
        "modified_proof_text",
    )

    def _write(self, event: dict) -> None:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        line = self._encode(event)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        if self.echo:
            print(line)

    def _encode(self, event: dict) -> str:
        """Serialise *event*, never raising.

        `default=str` covers objects json does not know (e.g. a pydantic REPL
        response that reached the logger unconverted). Anything that defeats
        even that -- a cycle, a __str__ that throws -- costs only the offending
        capture field, not the event.
        """
        try:
            return json.dumps(event, default=str)
        except (TypeError, ValueError):
            pass
        salvaged = dict(event)
        for key in self._BEST_EFFORT_FIELDS:
            if key in salvaged:
                salvaged[key] = None
        try:
            return json.dumps(salvaged, default=str)
        except (TypeError, ValueError):
            # Last resort: keep the identifying fields so the line still parses.
            return json.dumps({
                "proof_id":  str(event.get("proof_id")),
                "event":     str(event.get("event")),
                "timestamp": event["timestamp"],
                "log_error": "event was not serialisable",
            })
