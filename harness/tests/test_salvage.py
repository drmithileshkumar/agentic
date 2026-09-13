"""Mock tests for Groq tool_use_failed salvage.

Groq's harmony-format models (openai/gpt-oss-*) intermittently emit a channel
marker that Groq's own parser reads as a tool call, then reject with a 400:

    {'error': {'message': 'Tool choice is none, but model called a tool',
               'type': 'invalid_request_error', 'code': 'tool_use_failed',
               'failed_generation': '{"name": "assistant",
                                     "arguments": simp [inter_comm]}'}}

The completion never arrives, but the answer is in the body. Measured at 3 of
16 calls (~19%) on the 20260913_000127 Easy-5 run; every one carried a usable
tactic, and the retry that followed proved the theorem with the same text.

Contract under test:
  tool_use_failed 400   -> the tactic is recovered and the call succeeds
  every other error     -> untouched, still raises
  failed_generation is JSON-ish, not JSON: the arguments value is unquoted
  a salvaged step is logged as salvaged, never as a normal completion
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\lean")

import httpx
import openai

from harness.lean_agent import (
    TOOL_USE_FAILED_CODE, _SalvagedCompletion, _salvage_failed_generation,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_transcript_logging import (  # noqa: E402
    PS_NEW, THEOREM, FakeStepResult, LeanStub, make_agent, proved_elab,
    read_events,
)


def tool_use_failed(generation, code=TOOL_USE_FAILED_CODE):
    """Build the exact exception Groq raises, with the body shape it sends."""
    body = {"error": {"message": "Tool choice is none, but model called a tool",
                      "type": "invalid_request_error",
                      "code": code,
                      "failed_generation": generation}}
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
        json=body)
    return openai.BadRequestError("Error code: 400", response=response, body=body)


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_salvage_")

    # --------------------------------------------------------------- case 1
    print("Case 1: the real bodies from the 20260913_000127 run")
    for gen, want in [
        ('{"name": "assistant", "arguments": simp [inter_comm]}', "simp [inter_comm]"),
        ('{"name": "assistant<|channel|>final", "arguments": simpa using Nat.add_comm a b}',
         "simpa using Nat.add_comm a b"),
    ]:
        got = _salvage_failed_generation(tool_use_failed(gen))
        check("recovered %r" % want, got == want, "got %r" % got)

    print("Case 1b: shapes the parser must tolerate")
    for gen, want, label in [
        ('{"name": "a", "arguments": "exact h"}', "exact h", "quoted value"),
        ('{"name": "a", "arguments":    rfl   }', "rfl", "extra whitespace"),
        ('{"name": "a", "arguments": rw [Nat.add_comm, Nat.mul_comm]}',
         "rw [Nat.add_comm, Nat.mul_comm]", "brackets and commas"),
        ('{"name": "a", "arguments": exact fun a b => mul_comm a b}',
         "exact fun a b => mul_comm a b", "lambda with braces-free body"),
        ('{"name": "a",\n "arguments": intro h\n}', "intro h", "newlines"),
    ]:
        got = _salvage_failed_generation(tool_use_failed(gen))
        check("%s -> %r" % (label, want), got == want, "got %r" % got)

    print("Case 1c: unusable bodies yield None, never a crash")
    for gen, label in [
        ('{"name": "a", "arguments": }', "empty arguments"),
        ('{"name": "a"}', "no arguments key"),
        ("", "empty string"),
        ("not json at all", "garbage"),
    ]:
        got = _salvage_failed_generation(tool_use_failed(gen))
        check("%s -> None" % label, got is None, "got %r" % got)
    check("non-string failed_generation -> None",
          _salvage_failed_generation(tool_use_failed(None)) is None)

    # --------------------------------------------------------------- case 2
    print("Case 2: salvage is gated on the exact error code")
    check("a 400 with a different code is not salvaged",
          _salvage_failed_generation(
              tool_use_failed('{"name": "a", "arguments": simp}',
                              code="context_length_exceeded")) is None)
    for exc, label in [
        (openai.NotFoundError("404", response=httpx.Response(
            404, request=httpx.Request("POST", "https://x")), body=None), "404"),
        (openai.APIConnectionError(message="down", request=httpx.Request("POST", "https://x")),
         "APIConnectionError"),
        (ValueError("nope"), "ValueError"),
        (RuntimeError("boom"), "RuntimeError"),
    ]:
        check("%s is not salvaged" % label, _salvage_failed_generation(exc) is None)

    # --------------------------------------------------------------- case 3
    print("Case 3: a salvaged call succeeds instead of burning the step")
    agent = make_agent(
        [tool_use_failed('{"name": "assistant", "arguments": simpa using Nat.add_comm a b}')],
        LeanStub(proved_elab(),
                 [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)]),
        Path(tmp) / "case3.jsonl", max_steps=4)
    agent._sleep = lambda s: None
    result = agent.prove(THEOREM)
    events = read_events(Path(tmp) / "case3.jsonl")
    steps = [e for e in events if e["event"] == "step"]

    check("proof succeeded on the salvaged tactic",
          result.outcome == "PROVED", result.outcome)
    check("one step, not two (old code wasted one)", len(steps) == 1, len(steps))
    check("the salvaged text became the tactic",
          steps[0]["tactic"] == "simpa using Nat.add_comm a b", steps[0]["tactic"])
    check("step outcome is proved", steps[0]["outcome"] == "proved", steps[0]["outcome"])

    print("Case 3b: the log says it was salvaged")
    attempt = steps[0]["retry_attempts"][0]
    check("attempt flagged salvaged", attempt.get("salvaged") is True, attempt)
    check("salvage source recorded",
          attempt.get("salvage_source") == "groq_tool_use_failed", attempt)
    check("raw_response is the recovered text",
          attempt["raw_response"] == "simpa using Nat.add_comm a b", attempt)
    check("a normal completion is NOT flagged",
          "salvaged" not in json.dumps(_normal_attempt(tmp)), "flag leaked")

    # --------------------------------------------------------------- case 4
    print("Case 4: an unsalvageable 400 still fails the step")
    agent = make_agent(
        [tool_use_failed('{"name": "a", "arguments": }')] * 4,
        LeanStub(proved_elab(), []), Path(tmp) / "case4.jsonl", max_steps=6)
    agent._sleep = lambda s: None
    result = agent.prove(THEOREM)
    check("still FAILED_LLM_ERROR", result.outcome == "FAILED_LLM_ERROR", result.outcome)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


def _normal_attempt(tmp):
    """A step driven by an ordinary completion -- must carry no salvage flag."""
    agent = make_agent(
        ["rfl"],
        LeanStub(proved_elab(),
                 [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)]),
        Path(tmp) / "normal.jsonl", max_steps=2)
    agent.prove(THEOREM)
    events = read_events(Path(tmp) / "normal.jsonl")
    steps = [e for e in events if e["event"] == "step"]
    return steps[0]["retry_attempts"]


if __name__ == "__main__":
    raise SystemExit(run())
