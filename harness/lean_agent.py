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

and, verbatim, both halves of the conversation that produced it:
  - the exact system and user prompts sent to the model each step
  - the model's raw completion, before tactic extraction
  - every retried completion and why it was discarded
  - the history the model was shown at that step
  - Lean's raw response to the theorem's elaboration

so a JSONL log is a complete transcript, not a summary of one.

Outcome taxonomy
----------------
  proved          tactic closed the goal
  advanced        goal state changed (progress made)
  no_progress     Lean accepted the tactic but goal state is identical
  error           Lean returned error messages (type error, parse error, etc.),
                  or the LLM call itself failed (404, rate limit, timeout)
  rejected        lean_interact raised an exception (protocol-level failure)
  connectivity_error  the endpoint could not be reached; the step is retried
                  after a backoff rather than counted against the model

A step whose LLM call fails is logged rather than crashing the run, so one bad
API response cannot take down a whole batch and lose summary.json. Which of two
budgets it spends depends on why it failed:

  model failure       the endpoint answered with nothing usable (empty
                      completion after retries, 404, bad model name). Logged as
                      `error`; after MAX_CONSECUTIVE_LLM_FAILURES in a row the
                      proof aborts with FAILED_LLM_ERROR, so a persistent 404
                      does not burn the tactic budget and then misreport
                      FAILED_BUDGET_EXHAUSTED.
  connectivity failure  the endpoint was unreachable. Logged as
                      `connectivity_error` and retried after an exponential
                      backoff; only after MAX_CONSECUTIVE_CONNECTIVITY_FAILURES
                      does the proof abort, with FAILED_CONNECTIVITY_OUTAGE.

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

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
import openai
from lean_interact import Command, LeanREPLConfig, LeanServer, LocalProject, ProofStep
from lean_interact.interface import LeanError

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

# ---------------------------------------------------------------------------
# Failure budgets
#
# Two different things can go wrong with an LLM call, and conflating them is
# expensive. In the 20260906_191021 run a ~40-second NIM outage destroyed 12
# consecutive theorems: three connection errors tripped the one circuit breaker
# there was, each proof aborted in ~4 seconds, and the batch marched on through
# the backlog without ever pausing. So they are now counted separately.
#
#   model failure         the endpoint answered, but nothing usable came back
#                         (empty completion after retries, 404, bad model name,
#                         auth rejection). Retrying will not help -- fail fast.
#   connectivity failure  the endpoint could not be reached at all (connection
#                         refused, read timeout, 5xx). The theorem is fine; the
#                         network is not. Wait it out.
# ---------------------------------------------------------------------------

# Abort a proof after this many consecutive *model* failures. The openai client
# already retries 429/5xx internally, so reaching this means the endpoint or the
# model name is wrong, not that it is briefly busy.
MAX_CONSECUTIVE_LLM_FAILURES = 3

# Abort a proof after this many consecutive *connectivity* failures. With the
# backoff schedule below that is roughly 32 minutes of sustained outage -- long
# enough to ride out a blip, short enough not to burn a night on a dead endpoint.
MAX_CONSECUTIVE_CONNECTIVITY_FAILURES = 10

# Exponential backoff between connectivity retries: 30, 60, 120, 240, then held
# at the cap. Total wait across all 10 attempts is ~32 minutes.
CONNECTIVITY_BACKOFF_BASE = 30.0
CONNECTIVITY_BACKOFF_CAP = 300.0

# Hard ceiling on a single completion request. Proof 0024 of the 20260906_191021
# run consumed 22.3 hours inside one call: with no timeout the client waited on a
# socket that was never going to answer. 15 steps x 600s bounds a proof at 2.5h.
LLM_CALL_TIMEOUT_SECONDS = 600

# Hard ceiling on a single REPL command. lean_interact's LeanServer.run() takes
# `timeout` as a keyword, so this is enforced rather than merely hoped for. It
# does not cover config build / `import Mathlib`, which happen at construction.
LEAN_CALL_TIMEOUT_SECONDS = 120

# Completion budget per tactic request.
# Nemotron-3-Ultra-550B emits ~1700+ reasoning tokens on set-theory goals before
# committing to a tactic. 2048 truncated mid-reasoning on proof_0004.
MAX_TOKENS = 8192


# ---------------------------------------------------------------------------
# Tactic extraction
#
# A model that reasons out loud returns a paragraph, not a tactic. Sending the
# paragraph to Lean wastes a step on `unknown tactic`, so the tactic is pulled
# out of the prose here. The raw completion is still what gets logged as
# llm_raw_response; extraction only decides what Lean is asked to run.
# ---------------------------------------------------------------------------

# Tactic heads recognised as a definite start-of-tactic. Not exhaustive -- it
# does not need to be, because a short unpunctuated line qualifies anyway. Its
# job is to rescue tactics that are too wordy for the length heuristic, e.g.
# `exact Nat.add_comm _ _`.
TACTIC_KEYWORDS = frozenset({
    "rw", "simp", "apply", "exact", "intro", "intros", "cases", "induction",
    "constructor", "use", "ring", "omega", "decide", "rfl", "norm_num",
    "linarith", "nlinarith", "field_simp", "push_neg", "contrapose",
    "by_contra", "have", "obtain", "rcases", "ext", "funext", "congr",
    "refine", "left", "right", "trivial", "tauto", "aesop",
})

# Trailing punctuation that marks a line as a sentence rather than a tactic.
_SENTENCE_ENDINGS = (".", ",", ";", ":", "!", "?")

# Leading identifier of a line: letters and underscores up to the first other
# character, so `rw [h]` -> "rw" and `norm_num` -> "norm_num".
_HEAD_RE = re.compile(r"^[A-Za-z_]+")


def _line_head(line: str) -> str:
    match = _HEAD_RE.match(line)
    return match.group(0) if match else ""


def _looks_like_prose(line: str) -> bool:
    """True if *line* reads as a sentence rather than a tactic.

    Three signals, any of which is enough:
      - it ends in sentence punctuation
      - it has more than two spaces, i.e. more than three words
      - it opens with a capitalised word that is not a tactic keyword
    """
    if line.endswith(_SENTENCE_ENDINGS):
        return True
    if line.count(" ") > 2:
        return True
    first = line.split(" ", 1)[0] if " " in line else ""
    if first and first[:1].isupper() and first.lower() not in TACTIC_KEYWORDS:
        return True
    return False


# Words that legitimately follow a tactic head in Lean and are not narration.
# Without these, `simp only [h]` and `rw [h] at hx` read as prose.
LEAN_CONNECTIVES = frozenset({
    "at", "with", "using", "only", "by", "in", "generalizing",
})

# Characters that, immediately after a tactic head, mark a call rather than a
# sentence: argument brackets, a dotted path, a rewrite arrow, a numeral.
_CALL_STARTERS = "[(._←→⟨"


def _is_tactic_call(line: str, head: str) -> bool:
    """Decide whether a keyword-headed *line* is a tactic call or a sentence.

    A keyword head is necessary but not sufficient -- the model narrates *about*
    tactics constantly ("simp will rewrite the goal", "exact because the term
    has type"), and accepting those on the strength of the first word is what
    sent a 200-character sentence to Lean on proof_0004 step 3.

    What follows the head decides it. A call is followed by nothing, by argument
    syntax, or by a Lean identifier; narration is followed by an English word.
    """
    rest = line[len(head):]
    if not rest:
        return True                       # bare tactic: `omega`, `rfl`
    if rest[0] in _CALL_STARTERS or rest[0].isdigit():
        return True                       # `rw[h]`, `exact.foo`, `use 5`
    if not rest[0].isspace():
        # Anything else glued to the head is not a call -- notably a stray
        # backtick, which is how `` `simp` will rewrite ... `` slipped through.
        return False

    nxt = rest.lstrip()
    if not nxt:
        return True                       # trailing whitespace only
    if not nxt[0].isalpha():
        return True                       # `simp [h]`, `refine ⟨_, _⟩`, `use 5`

    word = nxt.split()[0]
    if any(c in word for c in "._[]()") or any(c.isdigit() for c in word):
        return True                       # `apply Nat.le_of_lt`, `exact h.symm`
    if len(word) == 1:
        return True                       # `intro h`, `ext x`, `have h : ...`
    if word.lower() in LEAN_CONNECTIVES:
        return True                       # `simp only [h]`, `rw [h] at hx`
    return False                          # `simp will rewrite`, `rw the goal`


def _is_tactic_line(line: str) -> bool:
    """True if *line* can plausibly be handed to Lean as a tactic."""
    # A known head is decided on its own terms -- `exact Nat.add_comm _ _` has
    # four spaces and is fine, `exact because the term ...` has four and is not.
    head = _line_head(line)
    if head in TACTIC_KEYWORDS:
        return _is_tactic_call(line, head)
    if _looks_like_prose(line):
        return False
    # Anything left that is short and unpunctuated -- `rfl`, `h.symm`, `⟨a, b⟩`.
    return len(line) <= 60


def _extract_tactic(raw: str) -> str:
    """Pull the intended tactic out of a raw LLM completion.

    Scans the non-empty lines from the bottom up and returns the last one that
    reads as a tactic, because a reasoning model states its answer last. If no
    line qualifies -- a completion truncated mid-thought, say -- the last
    non-empty line is returned so the caller still gets the model's own words
    and Lean's error names the real problem.

    Returns "" only for input that is entirely whitespace, fence markers or
    backticks; the caller's retry loop treats that as an empty completion.
    """
    if not raw or not raw.strip():
        return ""

    lines: list[str] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith("```"):      # ```lean / ``` fence markers
            continue
        line = line.strip("`").strip()  # inline backticks wrapping a tactic
        if line:
            lines.append(line)

    if not lines:
        return ""

    for line in reversed(lines):
        if _is_tactic_line(line):
            return line
    return lines[-1]


def _jsonable(obj: object) -> object:
    """Best-effort JSON-safe view of a lean_interact response object.

    lean_interact returns pydantic models, which json cannot encode directly.
    Preference order: pass through anything already serialisable, otherwise
    dump the model, otherwise fall back to ``str()``. Returns None only if even
    stringifying fails -- the log gets null rather than the run dying over a
    field that exists purely for the record.
    """
    if obj is None:
        return None

    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        pass

    for attr in ("model_dump", "dict"):
        dump = getattr(obj, attr, None)
        if callable(dump):
            try:
                # Round-trip so nested non-serialisable leaves are flattened
                # here rather than surprising the logger.
                return json.loads(json.dumps(dump(), default=str))
            except Exception:
                pass

    try:
        return str(obj)
    except Exception:
        return None


def _is_connectivity_error(exc: BaseException) -> bool:
    """True if *exc* means "could not reach the endpoint", not "bad request".

    The distinction drives which circuit breaker fires, so it errs toward
    connectivity only for causes that are genuinely transport-level. A 404, a
    401 or a bad model name is emphatically not one of them -- those should
    fail fast rather than sit through 32 minutes of backoff.

    Note openai.APITimeoutError subclasses openai.APIConnectionError, so the
    first check already covers per-call timeouts.
    """
    if isinstance(exc, openai.APIConnectionError):      # includes APITimeoutError
        return True
    if isinstance(exc, openai.InternalServerError):     # 5xx
        return True
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True                                     # ConnectError, ReadTimeout, ...
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    if isinstance(exc, OSError):                        # socket-level failures
        return True
    return False


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
    # "PROVED" | "FAILED_BUDGET_EXHAUSTED" | "FAILED_ELABORATION"
    # | "FAILED_LLM_ERROR" | "FAILED_CONNECTIVITY_OUTAGE"
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

        # Verbatim record of the most recent LLM exchange, refilled by
        # _get_tactic on every step and read back by prove() when it logs.
        self._last_exchange: dict = {}

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
        raw_elaboration = _jsonable(elab_resp)

        # A LeanError here has no `sorries` to read -- same shape mismatch as in
        # _apply_tactic. Treat it as a failed elaboration rather than crashing.
        if isinstance(elab_resp, LeanError):
            self.logger.error(
                proof_id, theorem_statement,
                detail=elab_resp.message or "Lean returned an error",
                raw=raw_elaboration,
            )
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

        if not elab_resp.sorries:
            # Lean rejected the statement itself (syntax/type error). The raw
            # response is the whole story here, so it rides on the error event
            # -- there is no start event on this path to carry it.
            detail = " | ".join(m.data for m in elab_resp.messages) if elab_resp.messages else "no sorry found"
            self.logger.error(proof_id, theorem_statement, detail=detail, raw=raw_elaboration)
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
        self.logger.start(
            proof_id, theorem_statement,
            goal=goal_text,
            lean_raw_elaboration=raw_elaboration,
        )

        history: list[dict] = []
        # Two independent budgets -- see the module constants for why.
        consecutive_model_failures = 0
        consecutive_connectivity_failures = 0

        # ---- 2. Proof loop ----------------------------------------------
        for step in range(1, self.max_steps + 1):

            # --- 2a. Ask the agent for a tactic (>=1 LLM call per step) ---
            try:
                tactic, llm_call_count = self._get_tactic(goal_text, history)
            except Exception as exc:
                # Log the step and keep going, so one bad API response costs a
                # step rather than the whole batch.
                # True number of API calls this step cost before it blew up,
                # recorded by _get_tactic; 1 if the exception could not carry it.
                llm_call_count = getattr(exc, "llm_call_count", 1)
                detail = f"LLM call failed: {type(exc).__name__}: {exc}"
                connectivity = _is_connectivity_error(exc)
                if connectivity:
                    consecutive_connectivity_failures += 1
                else:
                    consecutive_model_failures += 1

                # _get_tactic reset and part-filled this before it raised, so
                # the prompt it was sending -- and any completions it did get
                # back -- are still on the record for the failed step.
                exchange = self._last_exchange
                self._log_failed_step(
                    proof_id, theorem_statement, step, goal_text, detail,
                    outcome="connectivity_error" if connectivity else "error",
                    llm_calls=llm_call_count, exchange=exchange, history=history,
                )

                if connectivity:
                    # The theorem is fine, the endpoint is not. Never abort on a
                    # blip -- wait, and only give up on a sustained outage.
                    if consecutive_connectivity_failures >= MAX_CONSECUTIVE_CONNECTIVITY_FAILURES:
                        self.logger.error(proof_id, theorem_statement, detail=detail, raw=None)
                        self.logger.done(
                            proof_id, theorem_statement,
                            result="FAILED_CONNECTIVITY_OUTAGE", total_steps=step,
                        )
                        return self._make_result(
                            proof_id, theorem_statement, "FAILED_CONNECTIVITY_OUTAGE", step
                        )
                    self._connectivity_backoff(consecutive_connectivity_failures)
                    continue

                if consecutive_model_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                    self.logger.error(proof_id, theorem_statement, detail=detail, raw=None)
                    self.logger.done(
                        proof_id, theorem_statement,
                        result="FAILED_LLM_ERROR", total_steps=step,
                    )
                    return self._make_result(
                        proof_id, theorem_statement, "FAILED_LLM_ERROR", step
                    )
                continue

            # The endpoint answered, whatever it said -- connectivity is fine.
            consecutive_connectivity_failures = 0

            if not tactic.strip():
                # The API responded but the model gave nothing usable, even
                # after _get_tactic's retries. That is a model failure, and an
                # empty tactic cannot reach Lean anyway.
                consecutive_model_failures += 1
                detail = "LLM returned an empty tactic after retries"
                self._log_failed_step(
                    proof_id, theorem_statement, step, goal_text, detail,
                    outcome="error", llm_calls=llm_call_count,
                    exchange=self._last_exchange, history=history,
                )
                if consecutive_model_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                    self.logger.error(proof_id, theorem_statement, detail=detail, raw=None)
                    self.logger.done(
                        proof_id, theorem_statement,
                        result="FAILED_LLM_ERROR", total_steps=step,
                    )
                    return self._make_result(
                        proof_id, theorem_statement, "FAILED_LLM_ERROR", step
                    )
                continue

            consecutive_model_failures = 0

            # --- 2b. Send tactic to Lean (0 or 1 Lean calls) ---
            outcome, lean_messages, new_proof_state, new_goal, lean_call_count = (
                self._apply_tactic(tactic, proof_state)
            )

            # --- 2c. Log the step ----------------------------------------
            # Logged before history grows, so history_passed serialises as the
            # history the model actually saw this step.
            exchange = self._last_exchange
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
                llm_input        = exchange.get("llm_input"),
                llm_raw_response = exchange.get("llm_raw_response"),
                retry_attempts   = exchange.get("retry_attempts"),
                history_passed   = exchange.get("history_passed"),
            )

            history.append({
                "step":    step,
                "tactic":  tactic,
                "outcome": outcome,
                "message": lean_messages[0] if (lean_messages and outcome in ("error", "rejected")) else None,
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
        """Thin wrapper so we can intercept/count Lean calls in future.

        The REPL gets a hard timeout: a tactic that sends Lean into an
        unbounded search would otherwise stall the whole batch behind it.
        """
        return self._server.run(cmd, timeout=LEAN_CALL_TIMEOUT_SECONDS)

    def _log_failed_step(self, proof_id, theorem, step, goal_text, detail,
                         outcome, llm_calls, exchange, history) -> None:
        """Record a step that never reached Lean, and mirror it into history.

        Shared by the connectivity and model-failure paths so both keep the
        verbatim transcript fields and both stay visible to the next prompt.
        """
        self.logger.step(
            proof_id      = proof_id,
            theorem       = theorem,
            step          = step,
            tactic        = "",
            outcome       = outcome,
            goal_before   = goal_text,
            goal_after    = None,
            lean_messages = [detail],
            llm_calls     = llm_calls,
            lean_calls    = 0,   # never reached Lean
            llm_input        = exchange.get("llm_input"),
            llm_raw_response = exchange.get("llm_raw_response"),
            retry_attempts   = exchange.get("retry_attempts"),
            history_passed   = exchange.get("history_passed"),
        )
        history.append({
            "step":    step,
            "tactic":  "",
            "outcome": outcome,
            "message": detail,
        })

    def _sleep(self, seconds: float) -> None:
        """Indirection point so tests can observe backoff without waiting."""
        time.sleep(seconds)

    def _connectivity_backoff(self, attempt: int) -> float:
        """Wait out a transient outage; returns the delay actually used."""
        delay = min(CONNECTIVITY_BACKOFF_BASE * (2 ** (attempt - 1)),
                    CONNECTIVITY_BACKOFF_CAP)
        self._sleep(delay)
        return delay

    def _get_tactic(self, goal_text: str, history: list[dict]) -> tuple[str, int]:
        """Call the LLM and return (tactic, llm_call_count).

        Retries up to 3 extra times if the model returns an empty completion;
        the returned count reflects how many API calls that actually took, so
        the caller can log it accurately.

        If a call raises, the exception is tagged with ``llm_call_count`` before
        propagating, so the caller can log the true cost of the failed step too.

        The verbatim exchange -- prompt sent, every raw completion parsed, and
        the history the model was shown -- is recorded on ``self._last_exchange``
        rather than returned, so the (tactic, count) contract is unchanged. It
        is reset on entry, so the caller reading it after an exception sees what
        this step managed to say, never the previous step's.
        """
        self._last_exchange = {
            "llm_input":        None,
            "llm_raw_response": None,
            "retry_attempts":   [],
            # The list itself, not a copy: it is what the LLM was shown, and the
            # caller logs it before appending this step's own entry.
            "history_passed":   history,
        }

        history_text = "\n".join(
            f"Step {h.get('step', i + 1)}: tried `{h['tactic']}` -> {h['outcome']}"
            + (f"  [error: {h['message']}]" if h.get("message") else "")
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

        # Captured before the first call, so a step that dies mid-request still
        # logs what it was about to ask. Retries only append the nudge below,
        # which retry_attempts already accounts for.
        self._last_exchange["llm_input"] = {
            "system": self.system_prompt,
            "user":   user_msg,
        }

        # Ground truth for the metric: incremented on every API call issued,
        # including retries and the call that raises.
        calls = 0

        def _create():
            nonlocal calls
            calls += 1
            try:
                return self._client.chat.completions.create(
                    model=self.model, max_tokens=MAX_TOKENS, messages=messages,
                    timeout=LLM_CALL_TIMEOUT_SECONDS)
            except Exception as exc:
                _tag_call_count(exc, calls)
                raise

        attempts: list[dict] = self._last_exchange["retry_attempts"]
        tactic = ""

        resp = _create()

        # Some models intermittently return an empty completion. An empty
        # tactic is always rejected by the ProofStep validator before it
        # ever reaches Lean, so it burns a step for nothing -- re-ask.
        # Every response fetched is parsed: 1 initial call + up to 3 retries.
        for _attempt in range(4):
            if _attempt:
                messages[-1]["content"] += "\n\n[Your last response was empty. Respond with EXACTLY ONE Lean 4 tactic and nothing else.]"
                resp = _create()

            raw = resp.choices[0].message.content
            # What the model actually said, before any extraction. A missing
            # completion is recorded as "" so the field is always a string.
            raw_text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
            # ...and what we decided it meant. Only this reaches Lean; the log
            # keeps both so a bad tactic can be blamed on the model or on us.
            tactic = _extract_tactic(raw_text)

            attempt_record = {
                "attempt":          _attempt + 1,
                "raw_response":     raw_text,
                "extracted_tactic": tactic,
            }
            if tactic:
                attempt_record["accepted"] = True
            else:
                attempt_record["reason_skipped"] = "empty_tactic"
            attempts.append(attempt_record)
            self._last_exchange["llm_raw_response"] = raw_text

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

        # The REPL answers with either a ProofStepResponse or a LeanError, and
        # LeanError carries `message` (str) instead of the `messages`/
        # `proof_status`/`proof_state` trio. Reading it as a ProofStepResponse
        # raises AttributeError and takes the whole batch down, so classify it
        # here: Lean did answer, and what it said was an error.
        if isinstance(result, LeanError):
            return "error", [result.message], None, None, 1

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
