"""
WholeProofAgent -- one-shot proof generation, as a contrast to the tactic loop.

LeanAgentHarness asks the model for one tactic at a time and feeds Lean's
answer back. This agent asks once for the entire proof body, splices it into
the theorem and has Lean check the whole thing. There is no goal state, no
history and no step budget: a proof attempt is one LLM exchange and one Lean
command.

Result taxonomy (the dict returned by attempt_proof)
----------------------------------------------------
  proved              Lean accepted the reconstructed theorem with no error
                      messages and no `sorry` anywhere in the proof body
  failed_sorry        the proof body contains `sorry` (or Lean reported a
                      sorry), so the proof is incomplete even if it elaborates
  failed_lean_error   Lean returned error messages, answered with a LeanError,
                      or the REPL call itself failed
  failed_llm_error    the model gave nothing usable (empty completion after
                      retries), or the API failed MAX_CONSECUTIVE_LLM_FAILURES
                      times in a row -- in which case the result is logged and
                      a RuntimeError is raised, carrying the dict as `.result`

Log events
----------
  start   goal is the theorem statement without its `:= by sorry` tail; there
          is no elaborated goal state in this mode
  step    exactly one, step=1, tactic=<proof body>, so the verbatim exchange
          (prompt, raw completion, retry attempts) and the call counts are on
          the record and the done event's totals are correct
  done    generation_mode="whole_proof"; `result` uses the log schema's
          upper-case vocabulary (PROVED / FAILED_SORRY / FAILED_LEAN_ERROR /
          FAILED_LLM_ERROR), the returned dict the lower-case one above

LLM failure handling mirrors lean_agent: a call that cannot reach the endpoint
is retried in place with backoff and not counted; a call the endpoint rejected
(404, bad model name, ...) counts toward the circuit breaker.
"""

from __future__ import annotations

import re
import textwrap
import time
from datetime import datetime, timezone

from lean_interact import Command
from lean_interact.interface import LeanError

from .lean_agent import (
    CONNECTIVITY_BACKOFF_FACTOR,
    CONNECTIVITY_RETRIES,
    DEFAULT_RETRY_DELAY,
    LEAN_ELABORATION_TIMEOUT_SECONDS,
    LLM_CALL_TIMEOUT_SECONDS,
    MAX_CONSECUTIVE_LLM_FAILURES,
    _SalvagedCompletion,
    _is_connectivity_error,
    _messages_report_failure,
    _salvage_failed_generation,
)
from .logger import ProofLogger

GENERATION_MODE = "whole_proof"

SYSTEM_PROMPT = (
    "You are a Lean 4 expert. Respond with ONLY the proof body — the tactics "
    "that go after 'by'. No explanation, no markdown, no code fences. "
    "The proof must be complete and correct."
)

EMPTY_RESPONSE_NUDGE = (
    "\n\n[Your last response was empty. Respond with ONLY the Lean 4 proof body "
    "and nothing else.]"
)

# One initial call plus this many re-asks when the completion comes back empty.
EMPTY_RESPONSE_RETRIES = 3

# Lower-case result (returned) -> upper-case result (logged on the done event).
_LOG_RESULT = {
    "proved":            "PROVED",
    "failed_sorry":      "FAILED_SORRY",
    "failed_lean_error": "FAILED_LEAN_ERROR",
    "failed_llm_error":  "FAILED_LLM_ERROR",
}

# `:= by sorry`, `:= by\n  sorry`, or a bare trailing `sorry`, at the end of the
# statement. Whitespace between the pieces is free.
_BY_SORRY_TAIL_RE = re.compile(r"\s*:=\s*by\s+sorry\s*$")
_SORRY_TAIL_RE    = re.compile(r"\s*sorry\s*$")
_BY_TAIL_RE       = re.compile(r"\s*:=\s*by\s*$")

_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*$")
_DECL_HEAD_RE = re.compile(r"^\s*(import|open|theorem|lemma|example)\b")


class LLMCircuitBreakerError(RuntimeError):
    """Raised when the API failed MAX_CONSECUTIVE_LLM_FAILURES times in a row.

    The attempt has already been logged (error + done) by the time this is
    raised; `result` is the dict attempt_proof would otherwise have returned.
    """

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


def _strip_sorry(theorem_statement: str) -> str:
    """Remove the `:= by sorry` tail so a proof body can be spliced back on.

    Tolerates `:= by\\n  sorry` and a bare trailing `sorry`; a statement
    without either is returned stripped of trailing whitespace, and a dangling
    `:= by` is removed too so reconstruction never doubles it.
    """
    text = theorem_statement.rstrip()
    text, n = _BY_SORRY_TAIL_RE.subn("", text)
    if n:
        return text
    text = _SORRY_TAIL_RE.sub("", text)
    return _BY_TAIL_RE.sub("", text)


def _clean_proof_body(raw: str) -> str:
    """Turn a raw completion into tactic lines ready to indent under `by`.

    Strips code fences, a leading `by`, and -- when the model echoed the whole
    theorem despite being told not to -- everything up to the header's
    `:= by`. Returns "" for a completion with nothing in it.
    """
    if not raw or not raw.strip():
        return ""
    lines = [ln for ln in raw.strip().splitlines() if not _FENCE_RE.match(ln)]
    text = "\n".join(lines).strip()
    if not text:
        return ""

    if _DECL_HEAD_RE.match(text):
        # Echoed declaration: the first `:= by` belongs to the header.
        m = re.search(r":=\s*by\b", text)
        text = text[m.end():] if m else text

    text = textwrap.dedent(text).strip("\n")
    if re.match(r"^by\s*$", text.splitlines()[0] if text else ""):
        text = "\n".join(text.splitlines()[1:])
    elif text.startswith("by "):
        text = text[3:]
    text = textwrap.dedent(text).strip()
    return text


def _reconstruct(theorem_without_sorry: str, proof_body: str) -> str:
    """`{theorem_without_sorry} := by\\n  {proof_body}`, every body line indented."""
    body = "\n".join(
        ("  " + ln) if ln.strip() else ln for ln in proof_body.splitlines()
    )
    return f"{theorem_without_sorry} := by\n{body}"


def _format_message(msg) -> str:
    severity = getattr(msg, "severity", None)
    data = getattr(msg, "data", msg)
    return f"{severity}: {data}" if severity else str(data)


def _lean_reports_error(messages) -> bool:
    """True if any message is an error.

    Uses the severity lean_interact attaches to Command responses; a message
    without one falls back to the keyword scan lean_agent uses for tactics.
    """
    for msg in messages or []:
        severity = getattr(msg, "severity", None)
        if severity is not None:
            if severity == "error":
                return True
        elif _messages_report_failure([getattr(msg, "data", msg)]):
            return True
    return False


class WholeProofAgent:
    """
    Asks the model for a complete proof and has Lean check it in one command.

    Parameters
    ----------
    llm_client : openai.OpenAI
        A client already pointed at the provider (same object LeanAgentHarness
        builds); only `chat.completions.create` is used.
    lean_server : lean_interact.LeanServer
        A running REPL. The theorem carries its own `import Mathlib`, so the
        check runs under the elaboration timeout, not the tactic one.
    logger : ProofLogger
    model_name : str
    max_tokens : int
        Completion budget; the whole proof must fit.
    """

    retry_delay: float = DEFAULT_RETRY_DELAY

    def __init__(
        self,
        llm_client,
        lean_server,
        logger: ProofLogger,
        model_name: str,
        max_tokens: int = 1024,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        self._client     = llm_client
        self._server     = lean_server
        self.logger      = logger
        self.model       = model_name
        self.max_tokens  = max_tokens
        self.retry_delay = retry_delay

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attempt_proof(self, theorem_statement: str, proof_id: str) -> dict:
        """Generate and check one whole proof of *theorem_statement*.

        *theorem_statement* is the full `import Mathlib ... := by sorry` text.
        Returns a dict with proof_id, theorem, result, proof_body,
        lean_messages, llm_calls and timestamp (see module docstring).

        Raises LLMCircuitBreakerError (a RuntimeError) if the API fails
        MAX_CONSECUTIVE_LLM_FAILURES times in a row; the attempt is logged
        first and the dict rides on the exception.
        """
        theorem_without_sorry = _strip_sorry(theorem_statement)

        self.logger.start(
            proof_id, theorem_statement,
            goal=theorem_without_sorry,
            lean_raw_elaboration=None,
        )

        # ---- 1. Ask for the proof ----------------------------------------
        try:
            proof_body, llm_calls, exchange = self._get_proof_body(theorem_without_sorry)
        except Exception as exc:
            # The circuit breaker tripped inside _get_proof_body. Put the
            # failed step and the summary on the record before raising, so the
            # log for this proof is complete even though the batch may stop.
            llm_calls = getattr(exc, "llm_call_count", 1)
            exchange  = self._last_exchange
            detail = f"LLM call failed: {type(exc).__name__}: {exc}"
            self._log_step(proof_id, theorem_statement, theorem_without_sorry,
                           "", "error", [detail], llm_calls, 0, exchange)
            self.logger.error(proof_id, theorem_statement, detail=detail, raw=None)
            result = self._finish(proof_id, theorem_statement, "failed_llm_error",
                                  None, [detail], llm_calls)
            raise LLMCircuitBreakerError(
                f"{MAX_CONSECUTIVE_LLM_FAILURES} consecutive LLM API failures; last: {detail}",
                result,
            ) from exc

        if not proof_body:
            detail = "LLM returned an empty proof body after retries"
            self._log_step(proof_id, theorem_statement, theorem_without_sorry,
                           "", "error", [detail], llm_calls, 0, exchange)
            return self._finish(proof_id, theorem_statement, "failed_llm_error",
                                None, [detail], llm_calls)

        # ---- 2. Check it with Lean ---------------------------------------
        full_theorem = _reconstruct(theorem_without_sorry, proof_body)
        result, lean_messages = self._check(full_theorem, proof_body)

        self._log_step(proof_id, theorem_statement, theorem_without_sorry,
                       proof_body, "proved" if result == "proved" else "error",
                       lean_messages, llm_calls, 1, exchange)
        return self._finish(proof_id, theorem_statement, result,
                            proof_body, lean_messages, llm_calls)

    # ------------------------------------------------------------------
    # LLM side
    # ------------------------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        """Indirection point so tests can observe backoff without waiting."""
        time.sleep(seconds)

    def _connectivity_backoff(self, retry: int) -> float:
        delay = self.retry_delay * CONNECTIVITY_BACKOFF_FACTOR ** (retry - 1)
        self._sleep(delay)
        return delay

    def _get_proof_body(self, theorem_without_sorry: str) -> tuple[str, int, dict]:
        """Call the LLM and return (proof_body, llm_call_count, exchange).

        proof_body is "" if every completion came back empty. Connectivity
        failures are retried in place and not counted; any other failure of a
        call counts toward MAX_CONSECUTIVE_LLM_FAILURES, after which the last
        exception propagates, tagged with `llm_call_count`.
        """
        user_msg = f"Prove this theorem:\n\n{theorem_without_sorry}"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ]
        self._last_exchange = {
            "llm_input":        {"system": SYSTEM_PROMPT, "user": user_msg},
            "llm_raw_response": None,
            "retry_attempts":   [],
        }
        attempts: list[dict] = self._last_exchange["retry_attempts"]

        calls = 0
        consecutive_failures = 0

        def _create():
            nonlocal calls, consecutive_failures
            while True:
                for retry in range(CONNECTIVITY_RETRIES + 1):
                    try:
                        resp = self._client.chat.completions.create(
                            model=self.model, max_tokens=self.max_tokens,
                            messages=messages, timeout=LLM_CALL_TIMEOUT_SECONDS)
                        calls += 1
                        consecutive_failures = 0
                        return resp
                    except Exception as exc:
                        recovered = _salvage_failed_generation(exc)
                        if recovered is not None:
                            calls += 1
                            consecutive_failures = 0
                            return _SalvagedCompletion(recovered)
                        if _is_connectivity_error(exc) and retry < CONNECTIVITY_RETRIES:
                            delay = self._connectivity_backoff(retry + 1)
                            if self.logger.echo:
                                print(f"  [connectivity] {type(exc).__name__}; waited "
                                      f"{delay:.0f}s, retrying ({retry + 1}/{CONNECTIVITY_RETRIES})")
                            continue
                        # The endpoint answered and rejected us, or the outage
                        # outlasted the backoff: either way one failed call.
                        if not _is_connectivity_error(exc):
                            calls += 1
                        consecutive_failures += 1
                        attempts.append({
                            "attempt":  len(attempts) + 1,
                            "error":    f"{type(exc).__name__}: {exc}",
                            "reason_skipped": "api_failure",
                        })
                        if consecutive_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                            try:
                                exc.llm_call_count = calls   # type: ignore[attr-defined]
                            except Exception:
                                pass
                            raise
                        break   # out of the connectivity loop; re-issue the call

        proof_body = ""
        resp = _create()
        for attempt in range(EMPTY_RESPONSE_RETRIES + 1):
            if attempt:
                messages[-1]["content"] += EMPTY_RESPONSE_NUDGE
                resp = _create()

            raw = resp.choices[0].message.content
            raw_text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
            proof_body = _clean_proof_body(raw_text)

            record = {
                "attempt":          len(attempts) + 1,
                "raw_response":     raw_text,
                "extracted_tactic": proof_body,
            }
            if getattr(resp, "salvaged", False):
                record["salvaged"] = True
                record["salvage_source"] = "groq_tool_use_failed"
            if proof_body:
                record["accepted"] = True
            else:
                record["reason_skipped"] = "empty_tactic"
            attempts.append(record)
            self._last_exchange["llm_raw_response"] = raw_text

            if proof_body:
                break

        return proof_body, calls, self._last_exchange

    # ------------------------------------------------------------------
    # Lean side
    # ------------------------------------------------------------------

    def _lean_run(self, cmd, timeout: float = LEAN_ELABORATION_TIMEOUT_SECONDS):
        return self._server.run(cmd, timeout=timeout)

    def _ensure_server_alive(self) -> bool:
        """Restart the REPL if a timeout killed it; True if it is usable."""
        server = getattr(self, "_server", None)
        if server is None:
            return False
        try:
            if server.is_alive():
                return True
        except Exception:
            pass
        try:
            server.restart()
            return True
        except Exception:
            return False

    def _check(self, full_theorem: str, proof_body: str) -> tuple[str, list[str]]:
        """Run *full_theorem* through Lean and classify. Returns (result, messages)."""
        try:
            resp = self._lean_run(Command(cmd=full_theorem),
                                  timeout=LEAN_ELABORATION_TIMEOUT_SECONDS)
        except Exception as exc:
            self._ensure_server_alive()
            return "failed_lean_error", [f"{type(exc).__name__}: {exc}"]

        if isinstance(resp, LeanError):
            return "failed_lean_error", [resp.message or "Lean returned an error"]

        raw_messages = list(getattr(resp, "messages", None) or [])
        lean_messages = [_format_message(m) for m in raw_messages]

        if "sorry" in proof_body or getattr(resp, "sorries", None):
            return "failed_sorry", lean_messages
        if _lean_reports_error(raw_messages):
            return "failed_lean_error", lean_messages
        return "proved", lean_messages

    # ------------------------------------------------------------------
    # Logging / result
    # ------------------------------------------------------------------

    def _log_step(self, proof_id, theorem, goal, proof_body, outcome,
                  lean_messages, llm_calls, lean_calls, exchange) -> None:
        self.logger.step(
            proof_id      = proof_id,
            theorem       = theorem,
            step          = 1,
            tactic        = proof_body,
            outcome       = outcome,
            goal_before   = goal,
            goal_after    = None,
            lean_messages = lean_messages,
            llm_calls     = llm_calls,
            lean_calls    = lean_calls,
            llm_input        = exchange.get("llm_input"),
            llm_raw_response = exchange.get("llm_raw_response"),
            retry_attempts   = exchange.get("retry_attempts"),
            history_passed   = [],
        )

    def _finish(self, proof_id, theorem, result, proof_body, lean_messages, llm_calls) -> dict:
        self.logger.done(
            proof_id, theorem,
            result=_LOG_RESULT[result],
            total_steps=1,
            generation_mode=GENERATION_MODE,
        )
        return {
            "proof_id":      proof_id,
            "theorem":       theorem,
            "result":        result,
            "proof_body":    proof_body,
            "lean_messages": list(lean_messages),
            "llm_calls":     llm_calls,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
        }
