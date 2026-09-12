"""Mock tests for _apply_tactic's outcome classification and lean_calls
accounting.

Needs no Lean server: builds a bare LeanAgentHarness via object.__new__ and
replaces _lean_run with a stub. The ProofStep validation path uses the REAL
lean_interact.ProofStep, so the "never reached Lean" case is driven by the
actual pydantic validator rather than a mock of it.

Contract under test:
  _apply_tactic -> (outcome, lean_messages, new_proof_state, new_goal,
                    lean_call_count)
  lean_call_count == 0  iff ProofStep validation failed (never left process)
  lean_call_count == 1  on every path that actually invoked the REPL
"""
import sys

sys.path.insert(0, r"C:\lean")

from harness.lean_agent import LeanAgentHarness as LeanAgent

# proof_state is an int in lean_interact's schema, not a string.
PS = 7
PS_NEW = 8


class FakeMsg:
    def __init__(self, data):
        self.data = data


class FakeResult:
    def __init__(self, proof_status=None, proof_state=None, goals=None, messages=None):
        self.proof_status = proof_status
        self.proof_state = proof_state
        self.goals = goals or []
        self.messages = [FakeMsg(m) for m in (messages or [])]


class Recorder:
    """Stub _lean_run: records invocations, returns or raises as scripted."""

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.count = 0

    def __call__(self, step, timeout=None):
        self.timeout = timeout
        self.count += 1
        if self.raises is not None:
            raise self.raises
        return self.result


def make_agent(recorder):
    a = object.__new__(LeanAgent)
    a._lean_run = recorder
    return a


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    print("Case 1: empty tactic -> validation fails, REPL never called, count 0")
    rec = Recorder(result=FakeResult())
    a = make_agent(rec)
    out = a._apply_tactic("", PS)
    check("tuple has 5 elements", len(out) == 5, len(out))
    outcome, msgs, nps, goal, n = out
    check("outcome == rejected", outcome == "rejected", outcome)
    check("lean_call_count == 0", n == 0, n)
    check("REPL genuinely not invoked", rec.count == 0, rec.count)
    check("carries validator message", msgs and "string_too_short" in msgs[0],
          msgs[0][:60] if msgs else "no message")
    check("no proof state", nps is None and goal is None)

    print("Case 2: REPL raises -> count 1 (it was called)")
    rec = Recorder(raises=RuntimeError("repl crashed"))
    a = make_agent(rec)
    outcome, msgs, nps, goal, n = a._apply_tactic("simp", PS)
    check("outcome == rejected", outcome == "rejected", outcome)
    check("lean_call_count == 1", n == 1, n)
    check("REPL invoked once", rec.count == 1, rec.count)
    check("carries exception text", msgs and "repl crashed" in msgs[0], msgs)

    print("Case 3: proved -> count 1")
    rec = Recorder(result=FakeResult(proof_status="Completed", proof_state=PS_NEW))
    a = make_agent(rec)
    outcome, msgs, nps, goal, n = a._apply_tactic("exact Nat.add_comm _ _", PS)
    check("outcome == proved", outcome == "proved", outcome)
    check("lean_call_count == 1", n == 1, n)
    check("returns new proof state", nps == PS_NEW, nps)
    check("REPL invoked once", rec.count == 1, rec.count)

    print("Case 4: Lean error messages -> count 1")
    rec = Recorder(result=FakeResult(proof_status=None, proof_state=PS_NEW,
                                     messages=["error: unknown identifier 'foo'"]))
    a = make_agent(rec)
    outcome, msgs, nps, goal, n = a._apply_tactic("exact foo", PS)
    check("outcome == error", outcome == "error", outcome)
    check("lean_call_count == 1", n == 1, n)
    check("message preserved", "unknown identifier" in msgs[0], msgs)

    print("Case 5: proof state unchanged -> no_progress, count 1")
    rec = Recorder(result=FakeResult(proof_status=None, proof_state=PS))
    a = make_agent(rec)
    outcome, msgs, nps, goal, n = a._apply_tactic("skip", PS)
    check("outcome == no_progress", outcome == "no_progress", outcome)
    check("lean_call_count == 1", n == 1, n)

    print("Case 6: proof state changed -> advanced, count 1, goal returned")
    rec = Recorder(result=FakeResult(proof_status=None, proof_state=PS_NEW,
                                     goals=["b + a = a + b"]))
    a = make_agent(rec)
    outcome, msgs, nps, goal, n = a._apply_tactic("rw [Nat.add_comm]", PS)
    check("outcome == advanced", outcome == "advanced", outcome)
    check("lean_call_count == 1", n == 1, n)
    check("new proof state", nps == PS_NEW, nps)
    check("new goal", goal == "b + a = a + b", goal)

    print("Case 7: every path returns a 5-tuple with int count")
    scenarios = [
        ("", Recorder(result=FakeResult()), 0),
        ("simp", Recorder(raises=RuntimeError("x")), 1),
        ("simp", Recorder(result=FakeResult(proof_status="Completed", proof_state=PS_NEW)), 1),
        ("simp", Recorder(result=FakeResult(messages=["error: bad"])), 1),
        ("simp", Recorder(result=FakeResult(proof_state=PS)), 1),
        ("simp", Recorder(result=FakeResult(proof_state=PS_NEW, goals=["g"])), 1),
    ]
    for tac, rec, want in scenarios:
        a = make_agent(rec)
        out = a._apply_tactic(tac, PS)
        ok = (len(out) == 5 and isinstance(out[4], int) and out[4] == want
              and out[4] == rec.count)
        check("tactic %-6r -> count %d == invocations" % (tac, want), ok,
              "got %r, invocations %d" % (out[4], rec.count))

    print("Case 8: regression -- the 20260818_090232 run's shape")
    # 5 empty tactics rejected pre-Lean, then one real proving tactic.
    # Old code logged lean_calls=6; truth is 1.
    total = 0
    for tac in ["", "", "", "", ""]:
        rec = Recorder(result=FakeResult())
        total += make_agent(rec)._apply_tactic(tac, PS)[4]
    rec = Recorder(result=FakeResult(proof_status="Completed", proof_state=PS_NEW))
    total += make_agent(rec)._apply_tactic("exact Nat.add_comm _ _", PS)[4]
    check("total lean_calls == 1, not 6", total == 1, total)

    # --------------------------------------------------------------- case 9
    print("Case 9: a failed tactic is never called 'advanced'")
    # Regression for the 20260909_002719 run: has_errors matched only the
    # literal word "error", so Lean's "Unknown identifier" sailed through as
    # progress and the harness adopted the broken state it returned. 22 steps
    # across the logs were misclassified this way; proof_0007 lost 13 of its
    # 15 steps to the corrupted state that followed.
    for msg in ("Unknown identifier `h5`",
                "Unknown constant `Set.mem_union_iff`",
                "failed to synthesize instance of type class Bot (Sylow 5 G)",
                "aesop: failed to prove the goal after exhaustive search.",
                "<input>:1:0: expected tactic",
                "invalid field notation",
                "Lean error: Application type mismatch"):
        rec = Recorder(result=FakeResult(proof_state=PS_NEW, goals=["g"],
                                        messages=[msg]))
        a = make_agent(rec)
        outcome, msgs, nps, goal, n = a._apply_tactic("simp", PS)
        short = " ".join(msg.split())[:44]
        check("error, not advanced: %s" % short, outcome == "error", outcome)
        check("no state adopted: %s" % short, nps is None, nps)

    print("Case 9b: informational messages are still progress")
    for msg in ("declaration uses `sorry`", "Goals accomplished!", "trace: foo"):
        rec = Recorder(result=FakeResult(proof_state=PS_NEW, goals=["g"],
                                         messages=[msg]))
        a = make_agent(rec)
        outcome, _, nps, _, _ = a._apply_tactic("simp", PS)
        check("still advanced: %r" % msg, outcome == "advanced", outcome)

    print("Case 9c: a silent state change is still advanced")
    rec = Recorder(result=FakeResult(proof_state=PS_NEW, goals=["g"], messages=[]))
    a = make_agent(rec)
    outcome, _, nps, goal, _ = a._apply_tactic("intro h", PS)
    check("no messages -> advanced", outcome == "advanced", outcome)
    check("state adopted", nps == PS_NEW, nps)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
