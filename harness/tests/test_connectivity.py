"""Mock tests for the split circuit breaker and per-call timeout.

Regression for the 20260906_191021 run: a ~40-second NIM outage produced three
consecutive APIConnectionErrors, tripped the single circuit breaker, and killed
12 theorems in 40 seconds -- 19 of 31 completed proofs came back
FAILED_LLM_ERROR for reasons that had nothing to do with the model. Separately,
proof_0024 sat inside one un-timed-out call for 22.3 hours.

Contract under test:
  connectivity failure   logged outcome "connectivity_error", retried after an
                         exponential backoff, never counted toward the model
                         circuit breaker, aborts only at
                         MAX_CONSECUTIVE_CONNECTIVITY_FAILURES with
                         FAILED_CONNECTIVITY_OUTAGE
  model failure          a non-connectivity exception, or an empty completion
                         after retries, still aborts at
                         MAX_CONSECUTIVE_LLM_FAILURES with FAILED_LLM_ERROR
  timeout                openai.APITimeoutError counts as connectivity
  counters               reset independently on a successful call
  every API call         carries timeout=LLM_CALL_TIMEOUT_SECONDS
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\lean")

import httpx
import openai

from harness import lean_agent
from harness.lean_agent import (
    CONNECTIVITY_BACKOFF_BASE, CONNECTIVITY_BACKOFF_CAP,
    LLM_CALL_TIMEOUT_SECONDS, MAX_CONSECUTIVE_CONNECTIVITY_FAILURES,
    MAX_CONSECUTIVE_LLM_FAILURES, _is_connectivity_error,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_transcript_logging import (  # noqa: E402
    GOAL, PS, PS_NEW, THEOREM, FakeSorry, FakeStepResult, LeanStub,
    make_agent, proved_elab, read_events,
)


def _request():
    return httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _response(status):
    return httpx.Response(status, request=_request())


def conn_error(msg="Connection error."):
    return openai.APIConnectionError(message=msg, request=_request())


def timeout_error():
    return openai.APITimeoutError(request=_request())


def server_error(status=504):
    return openai.InternalServerError(
        "Error code: %d" % status, response=_response(status), body=None)


def drive(script, tmpdir, name, step_results=None, max_steps=15):
    """Run prove() with a recorded (non-sleeping) backoff."""
    log_path = Path(tmpdir) / (name + ".jsonl")
    agent = make_agent(script, LeanStub(proved_elab(), step_results or []),
                       log_path, max_steps)
    slept = []
    agent._sleep = lambda s: slept.append(s)
    result = agent.prove(THEOREM)
    return result, read_events(log_path), slept, agent


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_conn_")

    # --------------------------------------------------------------- case 1
    print("Case 1: exception classification")
    for exc, want, label in [
        (conn_error(), True, "APIConnectionError"),
        (timeout_error(), True, "APITimeoutError"),
        (server_error(504), True, "InternalServerError 504"),
        (server_error(503), True, "InternalServerError 503"),
        (httpx.ConnectError("refused"), True, "httpx.ConnectError"),
        (httpx.ReadTimeout("slow"), True, "httpx.ReadTimeout"),
        (OSError("socket died"), True, "OSError"),
        (openai.RateLimitError("429", response=_response(429), body=None), True, "RateLimitError 429"),
        (openai.NotFoundError("404", response=_response(404), body=None), False, "404 NotFound"),
        (openai.AuthenticationError("401", response=_response(401), body=None), False, "401 auth"),
        (ValueError("bad model"), False, "ValueError"),
        (RuntimeError("boom"), False, "RuntimeError"),
    ]:
        check("%s -> connectivity=%s" % (label, want),
              _is_connectivity_error(exc) is want, _is_connectivity_error(exc))

    # --------------------------------------------------------------- case 2
    print("Case 2: a connectivity blip is retried, not fatal")
    result, events, slept, _ = drive(
        [conn_error(), conn_error(), conn_error(), "rfl"], tmp, "case2",
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    steps = [e for e in events if e["event"] == "step"]
    check("proof still succeeded", result.outcome == "PROVED", result.outcome)
    check("3 connectivity errors survived (old code aborted here)",
          sum(1 for s in steps if s["outcome"] == "connectivity_error") == 3,
          [s["outcome"] for s in steps])
    check("final step proved", steps[-1]["outcome"] == "proved", steps[-1]["outcome"])
    check("backoff applied once per failure", len(slept) == 3, slept)
    check("backoff is exponential 30/60/120", slept == [30.0, 60.0, 120.0], slept)
    check("connectivity step logged with the exception text",
          "APIConnectionError" in steps[0]["lean_messages"][0], steps[0]["lean_messages"])
    check("connectivity step reached no Lean call",
          steps[0]["lean_calls"] == 0, steps[0]["lean_calls"])
    check("transcript fields still recorded",
          all(k in steps[0] for k in
              ("llm_input", "llm_raw_response", "retry_attempts", "history_passed")))

    # --------------------------------------------------------------- case 3
    print("Case 3: sustained outage aborts with FAILED_CONNECTIVITY_OUTAGE")
    n = MAX_CONSECUTIVE_CONNECTIVITY_FAILURES
    result, events, slept, _ = drive([conn_error()] * (n + 5), tmp, "case3",
                                     max_steps=n + 5)
    steps = [e for e in events if e["event"] == "step"]
    done = [e for e in events if e["event"] == "done"][0]
    check("result is FAILED_CONNECTIVITY_OUTAGE",
          result.outcome == "FAILED_CONNECTIVITY_OUTAGE", result.outcome)
    check("done event agrees", done["result"] == "FAILED_CONNECTIVITY_OUTAGE", done["result"])
    check("aborted at exactly %d failures" % n, len(steps) == n, len(steps))
    check("all steps are connectivity_error",
          all(s["outcome"] == "connectivity_error" for s in steps))
    check("no backoff after the final failure", len(slept) == n - 1, len(slept))
    check("backoff caps at %.0fs" % CONNECTIVITY_BACKOFF_CAP,
          max(slept) == CONNECTIVITY_BACKOFF_CAP, max(slept))
    check("schedule is 30,60,120,240 then capped",
          slept[:4] == [30.0, 60.0, 120.0, 240.0] and set(slept[4:]) == {CONNECTIVITY_BACKOFF_CAP},
          slept)
    total = sum(slept)
    check("total wait is ~25+ minutes", total >= 25 * 60, "%.0fs" % total)

    # --------------------------------------------------------------- case 4
    print("Case 4: a timeout is connectivity, not a model failure")
    result, events, slept, _ = drive(
        [timeout_error(), timeout_error(), timeout_error(), timeout_error(), "rfl"],
        tmp, "case4", [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    steps = [e for e in events if e["event"] == "step"]
    check("4 timeouts did not trip the model breaker at 3",
          result.outcome == "PROVED", result.outcome)
    check("all 4 logged as connectivity_error",
          sum(1 for s in steps if s["outcome"] == "connectivity_error") == 4,
          [s["outcome"] for s in steps])
    check("timeouts backed off", len(slept) == 4, slept)

    # --------------------------------------------------------------- case 5
    print("Case 5: model failures still trip the breaker at %d" % MAX_CONSECUTIVE_LLM_FAILURES)
    result, events, slept, _ = drive(
        [openai.NotFoundError("404", response=_response(404), body=None)] * 6,
        tmp, "case5", max_steps=10)
    steps = [e for e in events if e["event"] == "step"]
    check("result is FAILED_LLM_ERROR", result.outcome == "FAILED_LLM_ERROR", result.outcome)
    check("aborted at %d steps" % MAX_CONSECUTIVE_LLM_FAILURES,
          len(steps) == MAX_CONSECUTIVE_LLM_FAILURES, len(steps))
    check("logged as error, not connectivity_error",
          all(s["outcome"] == "error" for s in steps), [s["outcome"] for s in steps])
    check("no backoff on model failures", slept == [], slept)

    print("Case 5b: an empty completion after retries is a model failure")
    # Each step burns 4 calls inside _get_tactic before returning "".
    result, events, slept, _ = drive([""] * 40, tmp, "case5b", max_steps=10)
    steps = [e for e in events if e["event"] == "step"]
    check("result is FAILED_LLM_ERROR", result.outcome == "FAILED_LLM_ERROR", result.outcome)
    check("aborted at %d steps" % MAX_CONSECUTIVE_LLM_FAILURES,
          len(steps) == MAX_CONSECUTIVE_LLM_FAILURES, len(steps))
    check("empty tactic never reached Lean",
          all(s["lean_calls"] == 0 for s in steps), [s["lean_calls"] for s in steps])
    check("reason recorded", "empty tactic" in steps[0]["lean_messages"][0].lower(),
          steps[0]["lean_messages"])
    check("retry_attempts still captured (4 per step)",
          len(steps[0]["retry_attempts"]) == 4, steps[0]["retry_attempts"])

    # --------------------------------------------------------------- case 6
    print("Case 6: the two counters reset independently")
    # 2 connectivity, 2 model, 2 connectivity, then success: neither budget is
    # reached because each success resets, and the kinds never pool together.
    result, events, slept, _ = drive(
        [conn_error(), conn_error(),
         openai.NotFoundError("404", response=_response(404), body=None),
         openai.NotFoundError("404", response=_response(404), body=None),
         conn_error(), conn_error(), "rfl"],
        tmp, "case6", [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    steps = [e for e in events if e["event"] == "step"]
    check("6 mixed failures did not abort", result.outcome == "PROVED", result.outcome)
    check("4 connectivity + 2 model logged",
          sum(1 for s in steps if s["outcome"] == "connectivity_error") == 4
          and sum(1 for s in steps if s["outcome"] == "error") == 2,
          [s["outcome"] for s in steps])
    # Only a *successful* call resets a counter. A model failure is still a
    # failure, so it must not hand the connectivity budget a free reset --
    # otherwise alternating error kinds would keep a dead endpoint alive
    # forever. The backoff therefore keeps climbing across it.
    check("a model failure does not reset the connectivity budget",
          slept == [30.0, 60.0, 120.0, 240.0], slept)

    print("Case 6b: a success resets the connectivity counter")
    result, events, slept, _ = drive(
        [conn_error(), conn_error(), "intro h", conn_error(), conn_error(), "rfl"],
        tmp, "case6b",
        [FakeStepResult(proof_state=PS_NEW, goals=["b + a = a + b"]),
         FakeStepResult(proof_status="Completed", proof_state=9)])
    check("proved after two separate blips", result.outcome == "PROVED", result.outcome)
    check("backoff restarted at 30 after the success",
          slept == [30.0, 60.0, 30.0, 60.0], slept)

    # --------------------------------------------------------------- case 7
    print("Case 7: every API call carries the per-call timeout")
    _, _, _, agent = drive(["rfl"], tmp, "case7",
                           [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    timeouts = agent._client.chat.completions.timeouts
    check("timeout passed on every call",
          timeouts and all(t == LLM_CALL_TIMEOUT_SECONDS for t in timeouts), timeouts)
    check("timeout is 600s", LLM_CALL_TIMEOUT_SECONDS == 600, LLM_CALL_TIMEOUT_SECONDS)
    check("15 steps x 600s bounds a proof at 2.5h",
          15 * LLM_CALL_TIMEOUT_SECONDS / 3600 == 2.5)

    print("Case 7b: the Lean REPL call carries a timeout too")
    calls = {}

    class Srv:
        def run(self, cmd, timeout=None):
            calls["timeout"] = timeout
            return FakeStepResult(proof_status="Completed", proof_state=PS_NEW)

    probe = object.__new__(lean_agent.LeanAgentHarness)
    probe._server = Srv()
    probe._lean_run("cmd")
    check("LeanServer.run got a timeout",
          calls.get("timeout") == lean_agent.LEAN_TACTIC_TIMEOUT_SECONDS, calls)

    # --------------------------------------------------------------- case 8
    print("Case 8: backoff schedule is well formed")
    probe = object.__new__(lean_agent.LeanAgentHarness)
    seen = []
    probe._sleep = lambda s: seen.append(s)
    for attempt in range(1, MAX_CONSECUTIVE_CONNECTIVITY_FAILURES + 1):
        probe._connectivity_backoff(attempt)
    check("monotonic non-decreasing", all(b >= a for a, b in zip(seen, seen[1:])), seen)
    check("starts at base", seen[0] == CONNECTIVITY_BACKOFF_BASE, seen[0])
    check("never exceeds cap", max(seen) <= CONNECTIVITY_BACKOFF_CAP, max(seen))

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
