"""Mock tests for the connectivity retry, the model circuit breaker and the
per-call timeout.

Regression for the 20260906_191021 run: a ~40-second NIM outage produced three
consecutive APIConnectionErrors, tripped the single circuit breaker, and killed
12 theorems in 40 seconds -- 19 of 31 completed proofs came back
FAILED_LLM_ERROR for reasons that had nothing to do with the model. Separately,
proof_0024 sat inside one un-timed-out call for 22.3 hours.

Contract under test:
  connectivity failure   retried in place, CONNECTIVITY_RETRIES times, after
                         retry_delay * 3**k (5s, 15s, 45s by default). A call
                         that succeeds on a retry leaves no trace in the log:
                         no connectivity_error step, retries not counted in
                         llm_calls. Only when every retry fails is one
                         connectivity_error step logged, and the proof aborts
                         with FAILED_CONNECTIVITY_OUTAGE.
  model failure          a non-connectivity exception, or an empty completion
                         after retries, is never retried with backoff and still
                         aborts at MAX_CONSECUTIVE_LLM_FAILURES with
                         FAILED_LLM_ERROR
  timeout                openai.APITimeoutError counts as connectivity
  retry_delay            scales the whole schedule
  every API call         carries timeout=LLM_CALL_TIMEOUT_SECONDS
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import openai

from harness import lean_agent
from harness.lean_agent import (
    CONNECTIVITY_RETRIES, DEFAULT_RETRY_DELAY,
    LLM_CALL_TIMEOUT_SECONDS, MAX_CONSECUTIVE_LLM_FAILURES, _is_connectivity_error,
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


def not_found():
    return openai.NotFoundError("404", response=_response(404), body=None)


def drive(script, tmpdir, name, step_results=None, max_steps=15, retry_delay=None):
    """Run prove() with a recorded (non-sleeping) backoff."""
    log_path = Path(tmpdir) / (name + ".jsonl")
    agent = make_agent(script, LeanStub(proved_elab(), step_results or []),
                       log_path, max_steps)
    if retry_delay is not None:
        agent.retry_delay = retry_delay
    slept = []
    agent._sleep = lambda s: slept.append(s)
    result = agent.prove(THEOREM)
    return result, read_events(log_path), slept, agent


def outcomes(steps):
    return [s["outcome"] for s in steps]


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_conn_")
    schedule = [DEFAULT_RETRY_DELAY * 3 ** k for k in range(CONNECTIVITY_RETRIES)]

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
        (not_found(), False, "404 NotFound"),
        (openai.AuthenticationError("401", response=_response(401), body=None), False, "401 auth"),
        (ValueError("bad model"), False, "ValueError"),
        (RuntimeError("boom"), False, "RuntimeError"),
    ]:
        check("%s -> connectivity=%s" % (label, want),
              _is_connectivity_error(exc) is want, _is_connectivity_error(exc))

    # --------------------------------------------------------------- case 2
    print("Case 2: a blip is retried inside the call and leaves no trace")
    result, events, slept, _ = drive(
        [conn_error(), conn_error(), conn_error(), "rfl"], tmp, "case2",
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    steps = [e for e in events if e["event"] == "step"]
    check("proof succeeded", result.outcome == "PROVED", result.outcome)
    check("one step, no connectivity_error step", outcomes(steps) == ["proved"], outcomes(steps))
    check("schedule is 5, 15, 45", slept == [5.0, 15.0, 45.0] == schedule, slept)
    check("retries not counted: step llm_calls == 1", steps[0]["llm_calls"] == 1, steps[0]["llm_calls"])
    check("retries not counted: proof llm_calls == 1", result.llm_calls == 1, result.llm_calls)
    check("no error event", not any(e["event"] == "error" for e in events))

    # --------------------------------------------------------------- case 3
    print("Case 3: an outage that outlasts every retry aborts the proof")
    result, events, slept, _ = drive([conn_error()] * (CONNECTIVITY_RETRIES + 5), tmp, "case3")
    steps = [e for e in events if e["event"] == "step"]
    done = [e for e in events if e["event"] == "done"][0]
    check("result is FAILED_CONNECTIVITY_OUTAGE",
          result.outcome == "FAILED_CONNECTIVITY_OUTAGE", result.outcome)
    check("done event agrees", done["result"] == "FAILED_CONNECTIVITY_OUTAGE", done["result"])
    check("exactly one step, logged as connectivity_error",
          outcomes(steps) == ["connectivity_error"], outcomes(steps))
    check("%d calls attempted, then gave up" % (CONNECTIVITY_RETRIES + 1),
          len(slept) == CONNECTIVITY_RETRIES, slept)
    check("no wait after the final attempt", slept == schedule, slept)
    check("failed attempts not counted as llm_calls", steps[0]["llm_calls"] == 0, steps[0]["llm_calls"])
    check("step carries the exception text",
          "APIConnectionError" in steps[0]["lean_messages"][0], steps[0]["lean_messages"])
    check("step reached no Lean call", steps[0]["lean_calls"] == 0, steps[0]["lean_calls"])
    check("transcript fields still recorded",
          all(k in steps[0] for k in
              ("llm_input", "llm_raw_response", "retry_attempts", "history_passed")))
    check("error event logged", any(e["event"] == "error" for e in events))

    # --------------------------------------------------------------- case 4
    print("Case 4: a timeout is connectivity, not a model failure")
    result, events, slept, _ = drive(
        [timeout_error(), timeout_error(), server_error(503), "rfl"],
        tmp, "case4", [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    steps = [e for e in events if e["event"] == "step"]
    check("timeouts and a 503 were retried to success", result.outcome == "PROVED", result.outcome)
    check("no connectivity_error step", outcomes(steps) == ["proved"], outcomes(steps))
    check("backed off 3 times", slept == schedule, slept)

    # --------------------------------------------------------------- case 5
    print("Case 5: model failures still trip the breaker at %d" % MAX_CONSECUTIVE_LLM_FAILURES)
    result, events, slept, _ = drive([not_found()] * 6, tmp, "case5", max_steps=10)
    steps = [e for e in events if e["event"] == "step"]
    check("result is FAILED_LLM_ERROR", result.outcome == "FAILED_LLM_ERROR", result.outcome)
    check("aborted at %d steps" % MAX_CONSECUTIVE_LLM_FAILURES,
          len(steps) == MAX_CONSECUTIVE_LLM_FAILURES, len(steps))
    check("logged as error, not connectivity_error",
          all(s["outcome"] == "error" for s in steps), outcomes(steps))
    check("no backoff on model failures", slept == [], slept)
    check("a rejected call still counts as an LLM call",
          all(s["llm_calls"] == 1 for s in steps), [s["llm_calls"] for s in steps])

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
    print("Case 6: connectivity and model failures mixed")
    # Step 1: a blip, then a 404 -> one model failure. Step 2: a 404. Step 3:
    # two blips, then success. The schedule restarts for every call.
    result, events, slept, _ = drive(
        [conn_error(), not_found(), not_found(), conn_error(), conn_error(), "rfl"],
        tmp, "case6", [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    steps = [e for e in events if e["event"] == "step"]
    check("did not abort", result.outcome == "PROVED", result.outcome)
    check("two model errors then proved, no connectivity_error",
          outcomes(steps) == ["error", "error", "proved"], outcomes(steps))
    check("schedule restarts per call", slept == [5.0, 5.0, 15.0], slept)

    print("Case 6b: blips in separate steps never reach the log")
    result, events, slept, _ = drive(
        [conn_error(), "intro h", conn_error(), conn_error(), "rfl"],
        tmp, "case6b",
        [FakeStepResult(proof_state=PS_NEW, goals=["b + a = a + b"]),
         FakeStepResult(proof_status="Completed", proof_state=9)])
    steps = [e for e in events if e["event"] == "step"]
    check("proved after two separate blips", result.outcome == "PROVED", result.outcome)
    check("only real steps logged", outcomes(steps) == ["advanced", "proved"], outcomes(steps))
    check("each step counted 1 call", [s["llm_calls"] for s in steps] == [1, 1],
          [s["llm_calls"] for s in steps])
    check("history shown to the model has no blips",
          all(h["outcome"] != "connectivity_error" for h in steps[1]["history_passed"]),
          steps[1]["history_passed"])

    # --------------------------------------------------------------- case 7
    print("Case 7: every API call carries the per-call timeout")
    _, _, _, agent = drive([conn_error(), "rfl"], tmp, "case7",
                           [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)])
    timeouts = agent._client.chat.completions.timeouts
    check("timeout passed on every call, retries included",
          len(timeouts) == 2 and all(t == LLM_CALL_TIMEOUT_SECONDS for t in timeouts), timeouts)
    check("timeout is 600s", LLM_CALL_TIMEOUT_SECONDS == 600, LLM_CALL_TIMEOUT_SECONDS)

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
    print("Case 8: retry_delay scales the schedule")
    result, events, slept, _ = drive(
        [conn_error()] * 3 + ["rfl"], tmp, "case8",
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)], retry_delay=60)
    check("60 -> 60, 180, 540", slept == [60.0, 180.0, 540.0], slept)
    check("still proved", result.outcome == "PROVED", result.outcome)
    probe = object.__new__(lean_agent.LeanAgentHarness)
    check("default comes from the class without __init__",
          probe.retry_delay == DEFAULT_RETRY_DELAY == 5.0, probe.retry_delay)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
