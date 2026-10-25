"""Mock tests for WholeProofAgent.

No network and no Lean: the OpenAI client and the LeanServer are stubs, so
the contract under test is the agent's own logic --

  - `:= by sorry` is stripped and the body is spliced back as
        {theorem} := by\\n  {body}
    with every body line indented
  - fences, a leading `by`, and an echoed theorem header are cleaned off
  - classification: proved / failed_sorry / failed_lean_error / failed_llm_error
  - empty completions are re-asked up to 3 times, counted, then failed_llm_error
  - three consecutive API failures trip the breaker: logged, then RuntimeError
  - the log holds start, one step, done(generation_mode="whole_proof")

Run:  harness-venv\\Scripts\\python.exe harness\\tests\\test_whole_proof_agent.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import openai  # noqa: E402
from lean_interact.interface import LeanError  # noqa: E402

from harness.logger import ProofLogger  # noqa: E402
from harness.whole_proof_agent import (  # noqa: E402
    WholeProofAgent, _clean_proof_body, _reconstruct, _strip_sorry,
)

THEOREM = "import Mathlib\n\ntheorem toy (a b : Nat) : a + b = b + a := by sorry"
STRIPPED = "import Mathlib\n\ntheorem toy (a b : Nat) : a + b = b + a"


# --------------------------------------------------------------------- fakes

class FakeMsg:
    def __init__(self, data, severity="error"):
        self.data = data
        self.severity = severity


class FakeCmdResp:
    def __init__(self, messages=(), sorries=()):
        self.messages = list(messages)
        self.sorries = list(sorries)


class LeanStub:
    """Records every Command and answers from a queue (or raises)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.cmds = []
        self.restarted = 0

    def run(self, cmd, timeout=None):
        self.cmds.append(cmd.cmd)
        ans = self.answers.pop(0)
        if isinstance(ans, BaseException):
            raise ans
        return ans

    def is_alive(self):
        return False

    def restart(self):
        self.restarted += 1


class _Resp:
    def __init__(self, content):
        msg = type("m", (), {"content": content})()
        self.choices = [type("c", (), {"message": msg})()]


class LLMStub:
    """Each queued item is a completion string or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.requests.append(kw)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)


def not_found():
    # A 404-shaped error: not a connectivity error, so it counts as a failure.
    request = httpx.Request("POST", "https://stub.invalid/v1/chat/completions")
    response = httpx.Response(404, request=request)
    return openai.NotFoundError("Error code: 404 - model not found", response=response, body=None)


def make_agent(tmp, name, llm_script, lean_answers):
    log = Path(tmp) / f"{name}.jsonl"
    logger = ProofLogger(str(log), echo=False)
    lean = LeanStub(lean_answers)
    llm = LLMStub(llm_script)
    agent = WholeProofAgent(llm, lean, logger, model_name="stub-model", max_tokens=64)
    agent._sleep = lambda s: None
    return agent, llm, lean, log


def read_events(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_wholeproof_")

    print("Case 0: helpers")
    check("strip `:= by sorry`", _strip_sorry(THEOREM) == STRIPPED, _strip_sorry(THEOREM))
    check("strip `:= by\\n  sorry`",
          _strip_sorry("theorem t : P := by\n  sorry\n") == "theorem t : P")
    check("strip bare trailing sorry", _strip_sorry("theorem t : P := by sorry  ") == "theorem t : P")
    check("fences + leading by cleaned",
          _clean_proof_body("```lean\nby\n  simp\n  ring\n```") == "simp\nring",
          repr(_clean_proof_body("```lean\nby\n  simp\n  ring\n```")))
    check("echoed theorem header cleaned",
          _clean_proof_body("theorem toy : P := by\n  omega") == "omega",
          repr(_clean_proof_body("theorem toy : P := by\n  omega")))
    check("multi-line body indented",
          _reconstruct("theorem t : P", "intro h\nexact h") == "theorem t : P := by\n  intro h\n  exact h")

    # --------------------------------------------------------------- case 1
    print("Case 1: proved")
    agent, llm, lean, log = make_agent(tmp, "proved", ["exact Nat.add_comm a b"], [FakeCmdResp()])
    out = agent.attempt_proof(THEOREM, "pid-1")
    check("result proved", out["result"] == "proved", out)
    check("proof_body returned", out["proof_body"] == "exact Nat.add_comm a b", out["proof_body"])
    check("llm_calls == 1", out["llm_calls"] == 1, out["llm_calls"])
    check("dict keys", set(out) == {"proof_id", "theorem", "result", "proof_body",
                                   "lean_messages", "llm_calls", "timestamp"}, set(out))
    check("theorem is the original statement", out["theorem"] == THEOREM)
    check("full theorem sent to Lean",
          lean.cmds == [STRIPPED + " := by\n  exact Nat.add_comm a b"], lean.cmds)
    req = llm.requests[0]
    check("system prompt as specified",
          req["messages"][0]["content"].startswith("You are a Lean 4 expert. Respond with ONLY the proof body"))
    check("user prompt as specified",
          req["messages"][1]["content"] == f"Prove this theorem:\n\n{STRIPPED}")
    check("model/max_tokens passed", req["model"] == "stub-model" and req["max_tokens"] == 64, req)
    ev = read_events(log)
    check("events start, step, done", [e["event"] for e in ev] == ["start", "step", "done"],
          [e["event"] for e in ev])
    check("done.generation_mode == whole_proof", ev[-1]["generation_mode"] == "whole_proof", ev[-1])
    check("done.result == PROVED", ev[-1]["result"] == "PROVED", ev[-1]["result"])
    check("done totals llm=1 lean=1",
          ev[-1]["total_llm_calls"] == 1 and ev[-1]["total_lean_calls"] == 1, ev[-1])
    check("step carries verbatim exchange",
          ev[1]["llm_raw_response"] == "exact Nat.add_comm a b"
          and ev[1]["llm_input"]["user"].startswith("Prove this theorem"), ev[1])

    # --------------------------------------------------------------- case 2
    print("Case 2: sorry in body -> failed_sorry even if Lean is quiet")
    agent, llm, lean, log = make_agent(tmp, "sorry", ["intro h\nsorry"],
                                       [FakeCmdResp(messages=[FakeMsg("declaration uses 'sorry'", "warning")])])
    out = agent.attempt_proof(THEOREM, "pid-2")
    check("result failed_sorry", out["result"] == "failed_sorry", out["result"])
    check("warning carried through", out["lean_messages"] == ["warning: declaration uses 'sorry'"],
          out["lean_messages"])
    check("done.result FAILED_SORRY", read_events(log)[-1]["result"] == "FAILED_SORRY")

    # --------------------------------------------------------------- case 3
    print("Case 3: Lean error message -> failed_lean_error")
    agent, llm, lean, log = make_agent(tmp, "leanerr", ["simp"],
                                       [FakeCmdResp(messages=[FakeMsg("unsolved goals")])])
    out = agent.attempt_proof(THEOREM, "pid-3")
    check("result failed_lean_error", out["result"] == "failed_lean_error", out["result"])
    check("messages carried", out["lean_messages"] == ["error: unsolved goals"], out["lean_messages"])

    print("Case 3b: LeanError response -> failed_lean_error")
    agent, llm, lean, log = make_agent(tmp, "leanerr2", ["simp"], [LeanError(message="boom")])
    out = agent.attempt_proof(THEOREM, "pid-3b")
    check("result failed_lean_error", out["result"] == "failed_lean_error", out["result"])
    check("LeanError message carried", out["lean_messages"] == ["boom"], out["lean_messages"])

    print("Case 3c: REPL raises (timeout) -> failed_lean_error, server restarted")
    agent, llm, lean, log = make_agent(tmp, "leanraise", ["simp"], [TimeoutError("Lean timed out")])
    out = agent.attempt_proof(THEOREM, "pid-3c")
    check("result failed_lean_error", out["result"] == "failed_lean_error", out["result"])
    check("restart attempted", lean.restarted == 1, lean.restarted)

    print("Case 3d: warning-only messages still count as proved")
    agent, llm, lean, log = make_agent(tmp, "warn", ["simp"],
                                       [FakeCmdResp(messages=[FakeMsg("unused variable", "warning")])])
    out = agent.attempt_proof(THEOREM, "pid-3d")
    check("result proved", out["result"] == "proved", out)

    # --------------------------------------------------------------- case 4
    print("Case 4: empty completions re-asked 3 times, then failed_llm_error")
    agent, llm, lean, log = make_agent(tmp, "empty", ["", "   ", "```\n```", None], [FakeCmdResp()])
    out = agent.attempt_proof(THEOREM, "pid-4")
    check("result failed_llm_error", out["result"] == "failed_llm_error", out["result"])
    check("proof_body is None", out["proof_body"] is None, out["proof_body"])
    check("llm_calls == 4", out["llm_calls"] == 4, out["llm_calls"])
    check("Lean never called", lean.cmds == [], lean.cmds)
    check("nudge appended on retry", "[Your last response was empty" in llm.requests[1]["messages"][1]["content"])
    ev = read_events(log)
    check("done logged with FAILED_LLM_ERROR",
          ev[-1]["event"] == "done" and ev[-1]["result"] == "FAILED_LLM_ERROR", ev[-1])

    print("Case 4b: one empty then a real body -> proved, llm_calls == 2")
    agent, llm, lean, log = make_agent(tmp, "empty2", ["", "omega"], [FakeCmdResp()])
    out = agent.attempt_proof(THEOREM, "pid-4b")
    check("result proved", out["result"] == "proved", out["result"])
    check("llm_calls == 2", out["llm_calls"] == 2, out["llm_calls"])

    # --------------------------------------------------------------- case 5
    print("Case 5: three consecutive API failures -> logged, RuntimeError")
    agent, llm, lean, log = make_agent(tmp, "breaker", [not_found(), not_found(), not_found()], [])
    raised = None
    try:
        agent.attempt_proof(THEOREM, "pid-5")
    except RuntimeError as exc:
        raised = exc
    check("RuntimeError raised", raised is not None)
    check("result rides on the exception",
          getattr(raised, "result", {}).get("result") == "failed_llm_error", getattr(raised, "result", None))
    check("llm_calls == 3 on the result", getattr(raised, "result", {}).get("llm_calls") == 3)
    check("exactly 3 API calls made", len(llm.requests) == 3, len(llm.requests))
    ev = read_events(log)
    check("events start, step, error, done",
          [e["event"] for e in ev] == ["start", "step", "error", "done"], [e["event"] for e in ev])
    check("done.result FAILED_LLM_ERROR + whole_proof",
          ev[-1]["result"] == "FAILED_LLM_ERROR" and ev[-1]["generation_mode"] == "whole_proof", ev[-1])

    print("Case 5b: two failures then success -> proved, breaker not tripped")
    agent, llm, lean, log = make_agent(tmp, "breaker2", [not_found(), not_found(), "omega"], [FakeCmdResp()])
    out = agent.attempt_proof(THEOREM, "pid-5b")
    check("result proved", out["result"] == "proved", out["result"])
    check("llm_calls == 3", out["llm_calls"] == 3, out["llm_calls"])

    print("Case 5c: connectivity errors are retried in place and not counted")
    conn = openai.APIConnectionError(request=None)
    agent, llm, lean, log = make_agent(tmp, "conn", [conn, conn, "omega"], [FakeCmdResp()])
    waits = []
    agent._sleep = lambda s: waits.append(s)
    out = agent.attempt_proof(THEOREM, "pid-5c")
    check("result proved", out["result"] == "proved", out["result"])
    check("llm_calls == 1 (retries uncounted)", out["llm_calls"] == 1, out["llm_calls"])
    check("backoff 5s, 15s", waits == [5.0, 15.0], waits)

    print()
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
