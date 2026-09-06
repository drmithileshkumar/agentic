"""Mock-client tests for _get_tactic's empty-completion retry and llm_calls
accounting.

Needs no QWEN_API_KEY, no network, no Lean server: builds a bare
LeanAgentHarness via object.__new__ and injects a fake OpenAI-shaped client.

Contract under test:
  _get_tactic -> (tactic, llm_call_count)
  llm_call_count == number of API calls actually issued, on every path
  on exception, exc.llm_call_count carries the same number
"""
import sys

sys.path.insert(0, r"C:\lean")

from harness.lean_agent import LeanAgentHarness as LeanAgent

# Sentinel used in scripts to mean "this call raises"
class Boom(Exception):
    pass


class UntaggableBoom(Exception):
    """Exception that genuinely rejects the tag -- exercises the fallback.

    NB: `__slots__ = ()` does NOT achieve this; Exception subclasses still get
    a __dict__ from BaseException, so the tag would succeed. Blocking setattr
    for that one name is the only reliable way to simulate an untaggable type.
    """

    def __setattr__(self, name, value):
        if name == "llm_call_count":
            raise AttributeError("read-only")
        object.__setattr__(self, name, value)


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


def make_agent(script):
    a = object.__new__(LeanAgent)
    a.model = "fake-model"
    a.system_prompt = "SYSTEM"
    a._client = FakeClient(script)
    return a


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    # ---------------------------------------------------------- happy paths
    print("Case 1: non-empty first call -> no retry")
    a = make_agent(["exact Nat.add_comm _ _"])
    t, n = a._get_tactic("a b : N |- a + b = b + a", [])
    check("returns tactic", t == "exact Nat.add_comm _ _", repr(t))
    check("count == 1", n == 1, n)
    check("count == actual", n == len(a._client.chat.completions.calls))

    print("Case 2: empty then non-empty -> nudge appended, count 2")
    a = make_agent(["", "simp"])
    t, n = a._get_tactic("goal", [])
    calls = a._client.chat.completions.calls
    check("returns simp", t == "simp", repr(t))
    check("count == 2", n == 2, n)
    check("count == actual", n == len(calls), len(calls))
    check("call 1 unnudged", "was empty" not in calls[0][-1]["content"])
    check("call 2 nudged", "Respond with EXACTLY ONE" in calls[1][-1]["content"])
    check("nudge on user msg", calls[1][-1]["role"] == "user")
    check("system msg untouched", calls[1][0]["content"] == "SYSTEM")

    print("Case 3: empty x2 -> count 3, nudge accumulates")
    a = make_agent(["", "", "ring"])
    t, n = a._get_tactic("goal", [])
    calls = a._client.chat.completions.calls
    check("returns ring", t == "ring", repr(t))
    check("count == 3", n == 3, n)
    check("nudge x2 by call 3",
          calls[2][-1]["content"].count("Respond with EXACTLY ONE") == 2,
          calls[2][-1]["content"].count("Respond with EXACTLY ONE"))

    print("Case 4: empty x3 -> 4th response parsed, count 4")
    a = make_agent(["", "", "", "simp"])
    t, n = a._get_tactic("goal", [])
    check("returns simp", t == "simp", repr(t))
    check("count == 4", n == 4, n)
    check("count == actual", n == len(a._client.chat.completions.calls))

    print("Case 5: always empty -> ('', 4), bounded at 4")
    a = make_agent(["", "", "", "", "", ""])
    t, n = a._get_tactic("goal", [])
    calls = a._client.chat.completions.calls
    check("returns ''", t == "", repr(t))
    check("count == 4", n == 4, n)
    check("exactly 4 calls issued", len(calls) == 4, len(calls))

    print("Case 6: None / whitespace / backticks count as empty")
    a = make_agent([None, "omega"])
    t, n = a._get_tactic("goal", [])
    check("None -> retry, returns omega", t == "omega" and n == 2, (t, n))
    a = make_agent(["  ``  ", "linarith"])
    t, n = a._get_tactic("goal", [])
    check("backticks -> retry, returns linarith", t == "linarith" and n == 2, (t, n))

    # ------------------------------------------------------- failure paths
    print("Case 7: raise on call 1 -> exc.llm_call_count == 1")
    a = make_agent([Boom("api down")])
    try:
        a._get_tactic("goal", [])
        check("raised", False, "no exception")
    except Exception as exc:
        check("propagates original type", type(exc) is Boom, type(exc).__name__)
        check("tagged count == 1", getattr(exc, "llm_call_count", None) == 1,
              getattr(exc, "llm_call_count", None))
        check("count == actual", getattr(exc, "llm_call_count", None)
              == len(a._client.chat.completions.calls))

    print("Case 8: 2 empties then raise -> exc.llm_call_count == 3")
    a = make_agent(["", "", Boom("timeout")])
    try:
        a._get_tactic("goal", [])
        check("raised", False, "no exception")
    except Exception as exc:
        check("tagged count == 3", getattr(exc, "llm_call_count", None) == 3,
              getattr(exc, "llm_call_count", None))
        check("count == actual", getattr(exc, "llm_call_count", None)
              == len(a._client.chat.completions.calls),
              len(a._client.chat.completions.calls))
        check("message preserved", "timeout" in str(exc), str(exc))

    print("Case 9: 3 empties then raise on 4th -> exc.llm_call_count == 4")
    a = make_agent(["", "", "", Boom("500")])
    try:
        a._get_tactic("goal", [])
        check("raised", False, "no exception")
    except Exception as exc:
        check("tagged count == 4", getattr(exc, "llm_call_count", None) == 4,
              getattr(exc, "llm_call_count", None))

    print("Case 10: untaggable exception -> tagging cannot crash, caller gets 1")
    a = make_agent(["", UntaggableBoom("no attrs")])
    try:
        a._get_tactic("goal", [])
        check("raised", False, "no exception")
    except Exception as exc:
        check("tagging failure did not mask original", type(exc) is UntaggableBoom,
              type(exc).__name__)
        check("attribute absent", not hasattr(exc, "llm_call_count"))
        check("caller fallback yields 1", getattr(exc, "llm_call_count", 1) == 1)

    print("Case 11: count == calls issued, exhaustive over shapes")
    for script, want in ([["a"], 1], [["", "a"], 2], [["", "", "a"], 3],
                         [["", "", "", "a"], 4], [["", "", "", ""], 4]):
        a = make_agent(script)
        _, n = a._get_tactic("goal", [])
        check("script %r -> %d" % (script, want),
              n == want and n == len(a._client.chat.completions.calls), n)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
