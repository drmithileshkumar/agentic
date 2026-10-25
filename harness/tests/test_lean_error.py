"""Mock tests for LeanError handling.

lean_interact's LeanServer.run() answers with either a ProofStepResponse /
CommandResponse (both carry `messages`, `sorries`, `proof_status`) or a
LeanError, which carries only `message: str`. Reading a LeanError as though it
were the former raises AttributeError and kills the whole batch mid-run.

Regression for exactly that: the 20260906_162316 smoke run died on
    AttributeError: 'LeanError' object has no attribute 'messages'
after the start event was written but before any step event was.

Contract under test:
  _apply_tactic on a LeanError -> ("error", [message], None, None, 1)
  prove() on a LeanError elaboration -> FAILED_ELABORATION, no crash
  neither path loses the log line that was already due
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lean_interact.interface import LeanError

from harness.lean_agent import LeanAgentHarness as LeanAgent
from harness.logger import ProofLogger

sys.path.insert(0, str(Path(__file__).parent))
from test_transcript_logging import (  # noqa: E402
    GOAL, PS, PS_NEW, THEOREM, FakeSorry, FakeStepResult, LeanStub,
    make_agent, proved_elab, read_events,
)


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_leanerror_")

    # --------------------------------------------------------------- case 1
    print("Case 1: LeanError from a tactic is classified, not raised")
    agent = object.__new__(LeanAgent)
    calls = []

    def stub(step, timeout=None):
        calls.append(step)
        return LeanError(message="unknown tactic 'frobnicate'")

    agent._lean_run = stub
    out = agent._apply_tactic("frobnicate", PS)
    check("returns a 5-tuple", len(out) == 5, len(out))
    outcome, msgs, nps, goal, n = out
    check("outcome == error", outcome == "error", outcome)
    check("message carried through",
          msgs == ["unknown tactic 'frobnicate'"], msgs)
    check("no new proof state", nps is None, nps)
    check("no new goal", goal is None, goal)
    check("counted as a real Lean call", n == 1, n)
    check("REPL was genuinely invoked", len(calls) == 1, len(calls))

    print("Case 1b: an empty LeanError message still yields a usable list")
    agent._lean_run = lambda step, timeout=None: LeanError()
    outcome, msgs, _, _, n = agent._apply_tactic("simp", PS)
    check("outcome == error", outcome == "error", outcome)
    check("messages is a one-element list", msgs == [""], msgs)

    # --------------------------------------------------------------- case 2
    print("Case 2: a mid-proof LeanError logs a step and the run continues")
    log_path = Path(tmp) / "case2.jsonl"
    agent = make_agent(
        ["frobnicate", "exact Nat.add_comm _ _"],
        LeanStub(proved_elab(), [
            LeanError(message="unknown tactic 'frobnicate'"),
            FakeStepResult(proof_status="Completed", proof_state=PS_NEW),
        ]),
        log_path,
        max_steps=4,
    )
    result = agent.prove(THEOREM)
    events = read_events(log_path)
    steps = [e for e in events if e["event"] == "step"]

    check("run survived the LeanError", result.outcome == "PROVED", result.outcome)
    check("two step events logged", len(steps) == 2, len(steps))
    check("step 1 outcome == error", steps[0]["outcome"] == "error", steps[0]["outcome"])
    check("step 1 carries Lean's message",
          steps[0]["lean_messages"] == ["unknown tactic 'frobnicate'"],
          steps[0]["lean_messages"])
    check("step 1 counted one Lean call", steps[0]["lean_calls"] == 1,
          steps[0]["lean_calls"])

    # the transcript fields must survive this path too
    for key in ("llm_input", "llm_raw_response", "retry_attempts", "history_passed"):
        check("errored step has %s" % key, steps[0].get(key) is not None,
              steps[0].get(key))
    check("step 2 saw the error in history",
          steps[1]["history_passed"][0]["message"] == "unknown tactic 'frobnicate'",
          steps[1]["history_passed"])
    check("the failed tactic reached the next prompt",
          "frobnicate" in steps[1]["llm_input"]["user"])

    # --------------------------------------------------------------- case 3
    print("Case 3: a LeanError at elaboration fails cleanly, no crash")
    log_path = Path(tmp) / "case3.jsonl"
    agent = make_agent(
        ["simp"],
        LeanStub(LeanError(message="unknown identifier 'Nat.frob'"), []),
        log_path,
    )
    result = agent.prove(THEOREM)
    events = read_events(log_path)

    check("outcome == FAILED_ELABORATION",
          result.outcome == "FAILED_ELABORATION", result.outcome)
    check("no steps taken", result.total_steps == 0, result.total_steps)
    check("no LLM call was wasted", result.llm_calls == 0, result.llm_calls)
    errors = [e for e in events if e["event"] == "error"]
    check("one error event", len(errors) == 1, len(errors))
    check("detail is Lean's message",
          errors[0]["detail"] == "unknown identifier 'Nat.frob'", errors[0]["detail"])
    check("raw elaboration captured",
          errors[0]["raw"] is not None and "Nat.frob" in json.dumps(errors[0]["raw"]),
          errors[0]["raw"])
    check("done event still written",
          [e for e in events if e["event"] == "done"])
    check("every line valid JSON", len(events) >= 2, len(events))

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
