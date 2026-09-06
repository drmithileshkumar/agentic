"""Mock tests for the verbatim-transcript fields in the JSONL log.

Needs no Lean server, no QWEN_API_KEY and no network: drives the real prove()
loop with a stubbed _lean_run and a fake OpenAI-shaped client, writing to a
throwaway JSONL, then reads the file back and asserts on what landed.

Contract under test:
  step  events carry llm_input / llm_raw_response / retry_attempts /
        history_passed
  start events carry lean_raw_elaboration
  llm_raw_response is what the model said BEFORE stripping
  retry_attempts records one entry per completion parsed, discarded ones
        tagged reason_skipped and the used one tagged accepted
  history_passed is the history AS OF that step -- it must not be back-filled
        by later steps even though the harness passes the live list
  nothing here can crash a run: an unserialisable elaboration response still
        produces a parseable line
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\lean")

from harness.lean_agent import LeanAgentHarness as LeanAgent
from harness.logger import ProofLogger

# proof_state is an int in lean_interact's schema, not a string.
PS = 7
PS_NEW = 8

THEOREM = "theorem toy (a b : Nat) : a + b = b + a := by sorry"
GOAL = "a b : Nat |- a + b = b + a"
SYSTEM = "SYSTEM PROMPT"


# --------------------------------------------------------------------- fakes

class FakeMsg:
    def __init__(self, data):
        self.data = data


class FakeSorry:
    def __init__(self, proof_state, goal):
        self.proof_state = proof_state
        self.goal = goal


class FakeElab:
    """Stands in for the Command response. model_dump() mimics pydantic."""

    def __init__(self, sorries=None, messages=None):
        self.sorries = sorries or []
        self.messages = [FakeMsg(m) for m in (messages or [])]

    def model_dump(self):
        return {
            "sorries": [{"proofState": s.proof_state, "goal": s.goal}
                        for s in self.sorries],
            "messages": [{"data": m.data} for m in self.messages],
        }


class OpaqueElab(FakeElab):
    """No model_dump, not JSON-encodable -- must degrade to str(), not crash."""

    model_dump = None

    def __repr__(self):
        return "<OpaqueElab sorries=%d>" % len(self.sorries)


class FakeStepResult:
    def __init__(self, proof_status=None, proof_state=None, goals=None, messages=None):
        self.proof_status = proof_status
        self.proof_state = proof_state
        self.goals = goals or []
        self.messages = [FakeMsg(m) for m in (messages or [])]


class LeanStub:
    """_lean_run stub: first call is the elaboration, the rest are tactics."""

    def __init__(self, elab, step_results):
        self.elab = elab
        self.step_results = list(step_results)
        self.calls = 0

    def __call__(self, cmd):
        self.calls += 1
        if self.calls == 1:
            return self.elab
        return self.step_results.pop(0) if self.step_results else FakeStepResult()


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, model, max_tokens, messages):
        self.calls.append([dict(m) for m in messages])
        item = self.script.pop(0) if self.script else ""
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)


class FakeClient:
    def __init__(self, script):
        self.chat = type("chat", (), {})()
        self.chat.completions = FakeCompletions(script)


def make_agent(llm_script, lean_stub, log_path, max_steps=10):
    a = object.__new__(LeanAgent)
    a.model = "fake-model"
    a.system_prompt = SYSTEM
    a.max_steps = max_steps
    a._echo = False
    a._last_exchange = {}
    a._client = FakeClient(llm_script)
    a._lean_run = lean_stub
    a.logger = ProofLogger(str(log_path), echo=False)
    return a


def read_events(log_path):
    """Parse the JSONL back. Every line must be valid JSON on its own."""
    return [json.loads(line)
            for line in Path(log_path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def drive(llm_script, elab, step_results, tmpdir, name, max_steps=10):
    log_path = Path(tmpdir) / (name + ".jsonl")
    agent = make_agent(llm_script, LeanStub(elab, step_results), log_path, max_steps)
    result = agent.prove(THEOREM)
    return result, read_events(log_path)


def proved_elab():
    return FakeElab(sorries=[FakeSorry(PS, GOAL)])


TRANSCRIPT_KEYS = ("llm_input", "llm_raw_response", "retry_attempts", "history_passed")


# ---------------------------------------------------------------------- cases

def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_transcript_")

    # --------------------------------------------------------------- case 1
    print("Case 1: a one-step proof logs all four transcript fields")
    _, events = drive(
        ["  `exact Nat.add_comm _ _`  "],
        proved_elab(),
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)],
        tmp, "case1",
    )
    steps = [e for e in events if e["event"] == "step"]
    check("one step event", len(steps) == 1, len(steps))
    s = steps[0]
    for key in TRANSCRIPT_KEYS:
        check("step has %s" % key, key in s, sorted(s))

    check("llm_input.system is the system prompt",
          s["llm_input"]["system"] == SYSTEM, s["llm_input"]["system"])
    check("llm_input.user carries the goal", GOAL in s["llm_input"]["user"])
    check("llm_input.user carries the history section",
          "History:" in s["llm_input"]["user"] and "(none yet)" in s["llm_input"]["user"])
    check("llm_raw_response is pre-strip verbatim",
          s["llm_raw_response"] == "  `exact Nat.add_comm _ _`  ",
          repr(s["llm_raw_response"]))
    check("tactic is the stripped form -- unchanged behaviour",
          s["tactic"] == "exact Nat.add_comm _ _", repr(s["tactic"]))
    check("retry_attempts has one accepted entry",
          s["retry_attempts"] == [{"attempt": 1,
                                   "raw_response": "  `exact Nat.add_comm _ _`  ",
                                   "accepted": True}],
          s["retry_attempts"])
    check("history_passed empty on step 1", s["history_passed"] == [],
          s["history_passed"])

    # existing fields must be untouched
    check("event type still 'step'", s["event"] == "step")
    for key in ("proof_id", "theorem", "tactic", "outcome", "goal_before",
                "goal_after", "lean_messages", "llm_calls", "lean_calls"):
        check("existing field %s intact" % key, key in s, sorted(s))

    # --------------------------------------------------------------- case 2
    print("Case 2: start event carries Lean's raw elaboration response")
    starts = [e for e in events if e["event"] == "start"]
    check("one start event", len(starts) == 1, len(starts))
    raw = starts[0].get("lean_raw_elaboration")
    check("lean_raw_elaboration present", "lean_raw_elaboration" in starts[0])
    check("dumped as a dict", isinstance(raw, dict), type(raw).__name__)
    check("carries the sorry goal",
          raw and raw["sorries"][0]["goal"] == GOAL, raw)
    check("goal field unchanged", starts[0]["goal"] == GOAL)

    # --------------------------------------------------------------- case 3
    print("Case 3: an empty completion is recorded, not silently dropped")
    _, events = drive(
        ["", "simp"],
        proved_elab(),
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)],
        tmp, "case3",
    )
    s = [e for e in events if e["event"] == "step"][0]
    check("two attempts recorded", len(s["retry_attempts"]) == 2, s["retry_attempts"])
    check("attempt 1 skipped as empty_tactic",
          s["retry_attempts"][0] == {"attempt": 1, "raw_response": "",
                                     "reason_skipped": "empty_tactic"},
          s["retry_attempts"][0])
    check("attempt 2 accepted",
          s["retry_attempts"][1] == {"attempt": 2, "raw_response": "simp",
                                     "accepted": True},
          s["retry_attempts"][1])
    check("no entry has both keys",
          all(("accepted" in a) != ("reason_skipped" in a) for a in s["retry_attempts"]))
    check("llm_raw_response is the accepted completion",
          s["llm_raw_response"] == "simp", repr(s["llm_raw_response"]))
    check("llm_calls agrees with attempt count",
          s["llm_calls"] == len(s["retry_attempts"]), s["llm_calls"])

    print("Case 3b: a None completion is recorded as an empty string")
    _, events = drive(
        [None, "ring"],
        proved_elab(),
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)],
        tmp, "case3b",
    )
    s = [e for e in events if e["event"] == "step"][0]
    check("None -> raw_response ''", s["retry_attempts"][0]["raw_response"] == "",
          s["retry_attempts"][0])

    print("Case 3c: four empties -> four skipped attempts, none accepted")
    _, events = drive(
        ["", "", "", ""],
        proved_elab(),
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)],
        tmp, "case3c", max_steps=1,
    )
    s = [e for e in events if e["event"] == "step"][0]
    check("four attempts recorded", len(s["retry_attempts"]) == 4, len(s["retry_attempts"]))
    check("all skipped",
          all(a.get("reason_skipped") == "empty_tactic" for a in s["retry_attempts"]))
    check("attempt numbers are 1..4",
          [a["attempt"] for a in s["retry_attempts"]] == [1, 2, 3, 4])

    # --------------------------------------------------------------- case 4
    print("Case 4: history_passed is a per-step snapshot, not back-filled")
    _, events = drive(
        ["simp", "omega", "linarith"],
        proved_elab(),
        [
            FakeStepResult(proof_state=PS, messages=["error: unknown identifier"]),
            FakeStepResult(proof_state=PS_NEW, goals=["b + a = a + b"]),
            FakeStepResult(proof_status="Completed", proof_state=9),
        ],
        tmp, "case4",
    )
    steps = [e for e in events if e["event"] == "step"]
    check("three step events", len(steps) == 3, len(steps))
    check("step 1 saw no history", steps[0]["history_passed"] == [],
          steps[0]["history_passed"])
    check("step 2 saw exactly one entry", len(steps[1]["history_passed"]) == 1,
          steps[1]["history_passed"])
    check("step 3 saw exactly two entries", len(steps[2]["history_passed"]) == 2,
          steps[2]["history_passed"])

    h1 = steps[1]["history_passed"][0]
    check("history entry has step/tactic/outcome",
          h1["step"] == 1 and h1["tactic"] == "simp" and h1["outcome"] == "error", h1)
    check("history entry carries Lean's message",
          h1["message"] == "error: unknown identifier", h1)
    check("advanced step's history entry has null message",
          steps[2]["history_passed"][1]["message"] is None,
          steps[2]["history_passed"][1])

    check("history reached the prompt verbatim",
          "tried `simp` -> error" in steps[1]["llm_input"]["user"],
          steps[1]["llm_input"]["user"])
    check("prompt shows the message too",
          "unknown identifier" in steps[1]["llm_input"]["user"])
    check("step 2 prompt carries the updated goal",
          "b + a = a + b" in steps[2]["llm_input"]["user"],
          steps[2]["llm_input"]["user"])
    check("each step logged its own raw response",
          [s["llm_raw_response"] for s in steps] == ["simp", "omega", "linarith"],
          [s["llm_raw_response"] for s in steps])

    # --------------------------------------------------------------- case 5
    print("Case 5: a failed LLM call still logs the prompt it was sending")
    _, events = drive(
        [RuntimeError("api down"), RuntimeError("api down"), RuntimeError("api down")],
        proved_elab(),
        [],
        tmp, "case5", max_steps=5,
    )
    steps = [e for e in events if e["event"] == "step"]
    check("failed steps still logged", len(steps) == 3, len(steps))
    s = steps[0]
    for key in TRANSCRIPT_KEYS:
        check("failed step has %s" % key, key in s, sorted(s))
    check("prompt captured despite the failure",
          s["llm_input"] and s["llm_input"]["system"] == SYSTEM, s["llm_input"])
    check("no completion to record", s["llm_raw_response"] is None,
          s["llm_raw_response"])
    check("no attempts parsed", s["retry_attempts"] == [], s["retry_attempts"])
    check("history_passed still present", s["history_passed"] == [],
          s["history_passed"])
    check("step 2 sees step 1's failure in history",
          len(steps[1]["history_passed"]) == 1
          and steps[1]["history_passed"][0]["outcome"] == "error",
          steps[1]["history_passed"])

    # --------------------------------------------------------------- case 6
    print("Case 6: elaboration failure records the raw response on the error event")
    _, events = drive(
        ["simp"],
        FakeElab(sorries=[], messages=["error: unknown constant 'Nat.foo'"]),
        [],
        tmp, "case6",
    )
    errors = [e for e in events if e["event"] == "error"]
    check("one error event", len(errors) == 1, len(errors))
    check("raw is the dumped elaboration", isinstance(errors[0]["raw"], dict),
          errors[0]["raw"])
    check("raw carries Lean's message",
          "Nat.foo" in json.dumps(errors[0]["raw"]), errors[0]["raw"])
    check("no start event on this path",
          not [e for e in events if e["event"] == "start"])

    # --------------------------------------------------------------- case 7
    print("Case 7: an unserialisable elaboration degrades to a string, no crash")
    _, events = drive(
        ["simp"],
        OpaqueElab(sorries=[FakeSorry(PS, GOAL)]),
        [FakeStepResult(proof_status="Completed", proof_state=PS_NEW)],
        tmp, "case7",
    )
    start = [e for e in events if e["event"] == "start"][0]
    check("fell back to str()", isinstance(start["lean_raw_elaboration"], str),
          type(start["lean_raw_elaboration"]).__name__)
    check("str() content preserved", "OpaqueElab" in start["lean_raw_elaboration"],
          start["lean_raw_elaboration"])
    check("run still completed", [e for e in events if e["event"] == "done"])

    # --------------------------------------------------------------- case 8
    print("Case 8: every line stays valid JSON with a timestamp")
    check("all lines parsed", len(events) >= 3, len(events))
    check("every event timestamped", all("timestamp" in e for e in events))
    check("event vocabulary unchanged",
          {e["event"] for e in events} <= {"start", "step", "done", "error"},
          {e["event"] for e in events})

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
