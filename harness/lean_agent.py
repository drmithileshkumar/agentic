"""
LeanAgentHarness -- clean interface between an LLM agent and Lean 4.

This is the production harness. It owns the proof loop and cleanly separates:
  - the Lean side  (LeanServer via lean_interact)
  - the agent side (Qwen 2.5, via an OpenAI-compatible endpoint)
  - the logging    (ProofLogger)

Every message that crosses the agent <-> Lean boundary is logged with:
  - the tactic proposed
  - the raw Lean response (messages, goal state)
  - the classified outcome
  - call counts per step (normalised communication)

Outcome taxonomy
----------------
  proved          tactic closed the goal
  advanced        goal state changed (progress made)
  no_progress     Lean accepted the tactic but goal state is identical
  error           Lean returned error messages (type error, parse error, etc.),
                  or the LLM call itself failed (404, rate limit, timeout)
  rejected        lean_interact raised an exception (protocol-level failure)

A step whose LLM call fails is logged as `error` with an empty tactic rather
than crashing the run, so one bad API response cannot take down a whole batch
and lose summary.json. After MAX_CONSECUTIVE_LLM_FAILURES such steps in a row
the proof aborts with FAILED_LLM_ERROR -- a persistent 404 should not silently
burn the whole tactic budget and then report FAILED_BUDGET_EXHAUSTED.

Usage
-----
    from harness import LeanAgentHarness

    harness = LeanAgentHarness(
        project_dir=r"C:\\lean\\MyMathlibProject",
        log_path="logs/run.jsonl",
        model="qwen2.5-72b-instruct",
        max_steps=10,
    )
    result = harness.prove(
        "theorem toy (a b : Nat) : a + b = b + a := by sorry"
    )
    print(result.outcome, result.total_steps)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import openai
from lean_interact import Command, LeanREPLConfig, LeanServer, LocalProject, ProofStep

from .logger import ProofLogger


# ---------------------------------------------------------------------------
# Model configuration
#
# Qwen 2.5 is reached through an OpenAI-compatible chat completions endpoint,
# so the `openai` package talks to it unchanged -- only `base_url` differs.
#
#   QWEN_API_KEY   required
#   QWEN_BASE_URL  provider endpoint; defaults to DashScope. For Together AI:
#                  https://api.together.xyz/v1
#   QWEN_MODEL     model name, e.g. qwen2.5-72b-instruct
#
# Note that model names are provider-specific: DashScope wants
# `qwen2.5-72b-instruct`, Together AI wants `Qwen/Qwen2.5-72B-Instruct`. Set
# QWEN_MODEL to match whichever base URL you point at.
# ---------------------------------------------------------------------------

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
TOGETHER_BASE_URL  = "https://api.together.xyz/v1"

DEFAULT_MODEL = os.environ.get("QWEN_MODEL", "qwen2.5-72b-instruct")

# Abort a proof after this many consecutive failed LLM calls. The openai client
# already retries 429/5xx internally, so reaching this means the endpoint or the
# model name is wrong, not that it is briefly busy.
MAX_CONSECUTIVE_LLM_FAILURES = 3


def _tag_call_count(exc: BaseException, count: int) -> None:
    """Record on *exc* how many LLM calls had been issued when it was raised.

    Lets the caller log the true cost of a step that died mid-retry instead of
    assuming 1. Best-effort: virtually all exceptions accept the attribute
    (including __slots__ subclasses, which still inherit BaseException's
    __dict__), but a type overriding __setattr__ can refuse it -- the caller
    falls back to 1 when the attribute is absent.
    """
    try:
        exc.llm_call_count = count          # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProofResult:
    """Everything about a single proof attempt."""
    proof_id:        str
    theorem:         str
    # "PROVED" | "FAILED_BUDGET_EXHAUSTED" | "FAILED_ELABORATION" | "FAILED_LLM_ERROR"
    outcome:         str
    total_steps:     int
    llm_calls:       int
    lean_calls:      int
    tactic_outcomes: dict[str, int]
    tactics_used:    list[str]

    @property
    def proved(self) -> bool:
        return self.outcome == "PROVED"

    @property
    def lean_calls_per_step(self) -> float:
        return self.lean_calls / self.total_steps if self.total_steps else 0.0

    @property
    def llm_calls_per_step(self) -> float:
        return self.llm_calls / self.total_steps if self.total_steps else 0.0


# ---------------------------------------------------------------------------
# Default prompts
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """\
You are proving a Lean 4 theorem one tactic at a time.
You will be shown the current goal state and a history of what you tried.
Respond with EXACTLY ONE Lean 4 tactic and nothing else -- no explanation,
no code fences, no markdown. Just the tactic text, e.g.:
    rw [Nat.add_comm]

If the previous step returned an error, read it carefully and adjust.
If the goal did not change, your tactic had no effect -- try something different."""


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class LeanAgentHarness:
    """
    Drives a single Lean 4 proof attempt using an LLM agent.

    Parameters
    ----------
    project_dir : str
        Path to the Lean project (must have a lakefile.toml).
    log_path : str
        JSONL file to append log events to.
    model : str
        Qwen model name, e.g. "qwen2.5-72b-instruct". Must match the provider
        named by QWEN_BASE_URL.
    max_steps : int
        Hard budget: stop after this many tactic steps regardless of outcome.
    system_prompt : str | None
        Override the default system prompt.
    echo : bool
        Whether the logger should also print to stdout.
    """

    def __init__(
        self,
        project_dir:   str,
        log_path:      str  = "logs/proof_log.jsonl",
        model:         str  = DEFAULT_MODEL,
        max_steps:     int  = 10,
        system_prompt: Optional[str] = None,
        echo:          bool = True,
    ):
        self.project_dir   = project_dir
        self.model         = model
        self.max_steps     = max_steps
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._echo         = echo
        self.logger        = ProofLogger(log_path, echo=echo)

        # Build the Lean server once and reuse across calls to prove().
        project     = LocalProject(directory=project_dir)
        config      = LeanREPLConfig(project=project, verbose=False)
        self._server = LeanServer(config)

        # Qwen 2.5 over an OpenAI-compatible endpoint.
        api_key = os.environ.get("QWEN_API_KEY")
        if not api_key:
            raise RuntimeError(
                "QWEN_API_KEY is not set. Export it before running the harness, "
                "e.g. in PowerShell:\n"
                '    $env:QWEN_API_KEY = "..."\n'
                "Optionally set QWEN_BASE_URL (default: DashScope) and "
                "QWEN_MODEL (default: qwen2.5-72b-instruct)."
            )
        self._client = openai.OpenAI(
            api_key  = api_key,
            base_url = os.environ.get("QWEN_BASE_URL", DASHSCOPE_BASE_URL),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prove(self, theorem_statement: str, log_path: Optional[str] = None) -> ProofResult:
        """
        Attempt to prove *theorem_statement*.

        The theorem must end with `by sorry` so that LeanInteract can extract
        the initial proof state from the sorry placeholder.

        If *log_path* is given, events for this proof go to that file instead of
        the one set at construction. This lets a caller reuse one harness -- and
        therefore one LeanServer, whose `import Mathlib` costs minutes -- across
        many theorems while still writing one JSONL per proof.

        Returns a ProofResult with the outcome and all accumulated stats.
        """
        if log_path is not None:
            self.logger = ProofLogger(log_path, echo=self._echo)

        proof_id = self.logger.new_proof_id()

        # ---- 1. Elaborate the theorem and get the initial goal ----------
        elab_resp = self._lean_run(Command(cmd=theorem_statement))

        if not elab_resp.sorries:
            # Lean rejected the statement itself (syntax/type error)
            detail = " | ".join(m.data for m in elab_resp.messages) if elab_resp.messages else "no sorry found"
            self.logger.error(proof_id, theorem_statement, detail=detail, raw=None)
            self.logger.done(proof_id, theorem_statement, result="FAILED_ELABORATION", total_steps=0)
            return ProofResult(
                proof_id=proof_id,
                theorem=theorem_statement,
                outcome="FAILED_ELABORATION",
                total_steps=0,
                llm_calls=0,
                lean_calls=1,
                tactic_outcomes={},
                tactics_used=[],
            )

        proof_state = elab_resp.sorries[0].proof_state
        goal_text   = elab_resp.sorries[0].goal
        self.logger.start(proof_id, theorem_statement, goal=goal_text)

        history: list[dict] = []
        consecutive_llm_failures = 0

        # ---- 2. Proof loop ----------------------------------------------
        for step in range(1, self.max_steps + 1):

            # --- 2a. Ask the agent for a tactic (>=1 LLM call per step) ---
            try:
                tactic, llm_call_count = self._get_tactic(goal_text, history)
            except Exception as exc:
                # Mirror how _apply_tactic handles Lean-side failures: log the
                # step and keep going, so a transient API problem costs one step
                # rather than the whole batch.
                consecutive_llm_failures += 1
                # True number of API calls this step cost before it blew up,
                # recorded by _get_tactic; 1 if the exception could not carry it.
                llm_call_count = getattr(exc, "llm_call_count", 1)
                detail = f"LLM call failed: {type(exc).__name__}: {exc}"
                self.logger.step(
                    proof_id      = proof_id,
                    theorem       = theorem_statement,
                    step          = step,
                    tactic        = "",
                    outcome       = "error",
                    goal_before   = goal_text,
                    goal_after    = None,
                    lean_messages = [detail],
                    llm_calls     = llm_call_count,
                    lean_calls    = 0,   # never reached Lean
                )
                history.append({"tactic": "", "outcome": "error", "error": detail})

                if consecutive_llm_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                    self.logger.error(proof_id, theorem_statement, detail=detail, raw=None)
                    self.logger.done(
                        proof_id, theorem_statement,
                        result="FAILED_LLM_ERROR", total_steps=step,
                    )
                    return self._make_result(
                        proof_id, theorem_statement, "FAILED_LLM_ERROR", step
                    )
                continue

            consecutive_llm_failures = 0

            # --- 2b. Send tactic to Lean (0 or 1 Lean calls) ---
            outcome, lean_messages, new_proof_state, new_goal, lean_call_count = (
                self._apply_tactic(tactic, proof_state)
            )

            # --- 2c. Log the step ----------------------------------------
            self.logger.step(
                proof_id    = proof_id,
                theorem     = theorem_statement,
                step        = step,
                tactic      = tactic,
                outcome     = outcome,
                goal_before = goal_text,
                goal_after  = new_goal if outcome != "proved" else None,
                lean_messages = lean_messages,
                llm_calls   = llm_call_count,
                lean_calls  = lean_call_count,
            )

            history.append({
                "tactic":  tactic,
                "outcome": outcome,
                "error":   lean_messages[0] if (lean_messages and outcome in ("error", "rejected")) else None,
            })

            if outcome == "proved":
                self.logger.done(proof_id, theorem_statement, result="PROVED", total_steps=step)
                return self._make_result(proof_id, theorem_statement, "PROVED", step)

            # Advance the proof state for the next step
            if new_proof_state and outcome == "advanced":
                proof_state = new_proof_state
                goal_text   = new_goal or goal_text

        # ---- 3. Budget exhausted ----------------------------------------
        self.logger.done(
            proof_id, theorem_statement,
            result="FAILED_BUDGET_EXHAUSTED",
            total_steps=self.max_steps,
        )
        return self._make_result(proof_id, theorem_statement, "FAILED_BUDGET_EXHAUSTED", self.max_steps)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _lean_run(self, cmd):
        """Thin wrapper so we can intercept/count Lean calls in future."""
        return self._server.run(cmd)

    def _get_tactic(self, goal_text: str, history: list[dict]) -> tuple[str, int]:
        """Call the LLM and return (tactic, llm_call_count).

        Retries up to 3 extra times if the model returns an empty completion;
        the returned count reflects how many API calls that actually took, so
        the caller can log it accurately.

        If a call raises, the exception is tagged with ``llm_call_count`` before
        propagating, so the caller can log the true cost of the failed step too.
        """
        history_text = "\n".join(
            f"Step {i + 1}: tried `{h['tactic']}` -> {h['outcome']}"
            + (f"  [error: {h['error']}]" if h.get("error") else "")
            for i, h in enumerate(history)
        ) or "(none yet)"

        user_msg = (
            f"Current goal:\n{goal_text}\n\n"
            f"History:\n{history_text}\n\n"
            f"Propose the next tactic."
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user",   "content": user_msg},
        ]

        # Ground truth for the metric: incremented on every API call issued,
        # including retries and the call that raises.
        calls = 0

        def _create():
            nonlocal calls
            calls += 1
            try:
                return self._client.chat.completions.create(
                    model=self.model, max_tokens=150, messages=messages)
            except Exception as exc:
                _tag_call_count(exc, calls)
                raise

        resp = _create()

        # Some models intermittently return an empty completion. An empty
        # tactic is always rejected by the ProofStep validator before it
        # ever reaches Lean, so it burns a step for nothing -- re-ask.
        # Every response fetched is parsed: 1 initial call + up to 3 retries.
        for _attempt in range(4):
            if _attempt:
                messages[-1]["content"] += "\n\n[Your last response was empty. Respond with EXACTLY ONE Lean 4 tactic and nothing else.]"
                resp = _create()
            tactic = (resp.choices[0].message.content or "").strip().strip("`").strip()
            if tactic:
                return tactic, calls
        return tactic, calls  # still empty after 4 calls; outer code handles it

    def _apply_tactic(
        self, tactic: str, proof_state: str
    ) -> tuple[str, list[str], Optional[str], Optional[str], int]:
        """
        Send *tactic* to Lean against *proof_state*.

        Returns (outcome, lean_messages, new_proof_state, new_goal,
        lean_call_count).

        Outcome classification:
            proved      proof_status == "Completed"
            advanced    proof_state changed
            no_progress proof_state unchanged but no errors
            error       Lean returned error/warning messages
            rejected    ProofStep validation failed, or the REPL raised

        lean_call_count is 0 only when the tactic never left this process --
        i.e. ProofStep validation rejected it, which is what an empty tactic
        does. Counting those as Lean calls inflates lean_calls_per_step, a
        primary benchmark metric, so the two rejection causes are split.
        """
        # Build the request first: a validation failure here (e.g. an empty
        # tactic) never reaches the REPL, so it must not count as a Lean call.
        try:
            step = ProofStep(tactic=tactic, proof_state=proof_state)
        except Exception as exc:
            return "rejected", [str(exc)], None, None, 0

        try:
            result = self._lean_run(step)
        except Exception as exc:
            # The REPL was called and failed (crash, timeout, protocol error).
            return "rejected", [str(exc)], None, None, 1

        messages = [m.data for m in result.messages] if result.messages else []
        has_errors = any(
            "error" in str(m).lower() for m in messages
        )

        if result.proof_status == "Completed":
            return "proved", messages, result.proof_state, None, 1

        if has_errors:
            return "error", messages, None, None, 1

        if result.proof_state == proof_state:
            return "no_progress", messages, None, None, 1

        new_goal = result.goals[0] if result.goals else None
        return "advanced", messages, result.proof_state, new_goal, 1

    def _make_result(self, proof_id: str, theorem: str, outcome: str, total_steps: int) -> ProofResult:
        return ProofResult(
            proof_id        = proof_id,
            theorem         = theorem,
            outcome         = outcome,
            total_steps     = total_steps,
            llm_calls       = self.logger.llm_calls_total(proof_id),
            lean_calls      = self.logger.lean_calls_total(proof_id),
            tactic_outcomes = self.logger.tactic_outcomes(proof_id),
            tactics_used    = self.logger._tactics_used[proof_id],
        )
