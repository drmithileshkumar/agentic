"""Mock tests for Lean REPL timeouts and server recovery.

Regression for the 20260908_183715 run, which died on theorem 1 of 50 without
writing a single proof file:

    TimeoutError: The Lean server did not respond in time (timeout=120)
      ... in prove() -> self._lean_run(Command(cmd=theorem_statement))

Two separate defects, both introduced with the per-call Lean timeout:
  1. one flat 120s budget was applied to the elaboration Command, which carries
     its own `import Mathlib` and measured ~230s in the 20260906_164928 run --
     so every elaboration timed out on the first try;
  2. that call was not inside a try/except, so the exception escaped prove(),
     escaped BatchRunner.run(), and killed the batch instead of the theorem.

Contract under test:
  elaboration    uses LEAN_ELABORATION_TIMEOUT_SECONDS, generous enough for a
                 command that re-imports Mathlib
  tactics        use LEAN_TACTIC_TIMEOUT_SECONDS
  any failure    is contained: the theorem returns FAILED_ELABORATION or
                 "rejected", and the batch continues
  after timeout  the killed REPL is restarted, because lean_interact calls
                 kill() on timeout and every later theorem would face a corpse
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\lean")

from harness import lean_agent
from harness.lean_agent import (
    LEAN_ELABORATION_TIMEOUT_SECONDS, LEAN_TACTIC_TIMEOUT_SECONDS,
    LeanAgentHarness as LeanAgent,
)
from harness.logger import ProofLogger

sys.path.insert(0, str(Path(__file__).parent))
from test_transcript_logging import (  # noqa: E402
    GOAL, PS, PS_NEW, THEOREM, FakeSorry, FakeStepResult, FakeElab,
    make_agent, proved_elab, read_events,
)


class RecordingServer:
    """Captures the timeout of every run(), and can fake a kill-on-timeout."""

    def __init__(self, script):
        self.script = list(script)
        self.timeouts = []
        self.alive = True
        self.restarts = 0

    def run(self, cmd, timeout=None):
        self.timeouts.append(timeout)
        item = self.script.pop(0) if self.script else FakeStepResult()
        if isinstance(item, BaseException):
            self.alive = False          # lean_interact kills the server here
            raise item
        return item

    def is_alive(self):
        return self.alive

    def restart(self):
        self.restarts += 1
        self.alive = True

    def kill(self):
        self.alive = False


def agent_with_server(server, tmpdir, name, script=None, max_steps=3):
    a = make_agent(script or ["rfl"], None, Path(tmpdir) / (name + ".jsonl"), max_steps)
    del a._lean_run                      # use the real one, so timeouts are passed
    a._server = server
    a._sleep = lambda s: None
    return a


def lean_timeout(t=120):
    return TimeoutError(
        "The Lean server did not respond in time (timeout=%s) and is now killed." % t)


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_leantimeout_")

    # --------------------------------------------------------------- case 1
    print("Case 1: the two Lean budgets are distinct and correctly ordered")
    check("elaboration budget is generous",
          LEAN_ELABORATION_TIMEOUT_SECONDS >= 600, LEAN_ELABORATION_TIMEOUT_SECONDS)
    check("tactic budget is tight",
          LEAN_TACTIC_TIMEOUT_SECONDS <= 300, LEAN_TACTIC_TIMEOUT_SECONDS)
    check("elaboration budget exceeds the tactic budget",
          LEAN_ELABORATION_TIMEOUT_SECONDS > LEAN_TACTIC_TIMEOUT_SECONDS)
    check("elaboration budget clears the ~230s measured import Mathlib",
          LEAN_ELABORATION_TIMEOUT_SECONDS > 230 * 2,
          LEAN_ELABORATION_TIMEOUT_SECONDS)
    check("the old flat 120s constant is gone",
          not hasattr(lean_agent, "LEAN_CALL_TIMEOUT_SECONDS"))

    # --------------------------------------------------------------- case 2
    print("Case 2: elaboration and tactics get their own timeouts")
    srv = RecordingServer([
        proved_elab(),
        FakeStepResult(proof_status="Completed", proof_state=PS_NEW),
    ])
    a = agent_with_server(srv, tmp, "case2")
    result = a.prove(THEOREM)
    check("proof succeeded", result.outcome == "PROVED", result.outcome)
    check("two REPL calls made", len(srv.timeouts) == 2, srv.timeouts)
    check("elaboration used the long budget",
          srv.timeouts[0] == LEAN_ELABORATION_TIMEOUT_SECONDS, srv.timeouts[0])
    check("tactic used the short budget",
          srv.timeouts[1] == LEAN_TACTIC_TIMEOUT_SECONDS, srv.timeouts[1])

    # --------------------------------------------------------------- case 3
    print("Case 3: an elaboration timeout fails the theorem, not the batch")
    srv = RecordingServer([lean_timeout()])
    a = agent_with_server(srv, tmp, "case3")
    try:
        result = a.prove(THEOREM)
        raised = None
    except Exception as exc:                      # the old behaviour
        result, raised = None, exc
    check("prove() did not propagate (old code crashed the batch here)",
          raised is None, repr(raised))
    check("result is FAILED_ELABORATION",
          result and result.outcome == "FAILED_ELABORATION",
          result.outcome if result else None)
    check("no steps attempted", result and result.total_steps == 0)

    events = read_events(Path(tmp) / "case3.jsonl")
    errs = [e for e in events if e["event"] == "error"]
    dones = [e for e in events if e["event"] == "done"]
    check("error event written", len(errs) == 1, len(errs))
    check("error names the timeout",
          errs and "TimeoutError" in errs[0]["detail"], errs[0]["detail"] if errs else None)
    check("done event still written -- summary.json survives",
          dones and dones[0]["result"] == "FAILED_ELABORATION")

    print("Case 3b: the killed server is restarted for the next theorem")
    check("server was killed by the timeout", srv.restarts == 1, srv.restarts)
    check("server is alive again", srv.alive is True)

    print("Case 3c: the batch really can continue after one bad theorem")
    srv = RecordingServer([
        lean_timeout(),                                        # theorem 1 dies
        proved_elab(),                                         # theorem 2 fine
        FakeStepResult(proof_status="Completed", proof_state=PS_NEW),
    ])
    a = agent_with_server(srv, tmp, "case3c")
    first = a.prove(THEOREM)
    a.logger = ProofLogger(str(Path(tmp) / "case3c_b.jsonl"), echo=False)
    second = a.prove(THEOREM)
    check("theorem 1 failed", first.outcome == "FAILED_ELABORATION", first.outcome)
    check("theorem 2 proved on the restarted server",
          second.outcome == "PROVED", second.outcome)
    check("exactly one restart", srv.restarts == 1, srv.restarts)

    # --------------------------------------------------------------- case 4
    print("Case 4: a tactic timeout is contained and revives the server")
    srv = RecordingServer([
        proved_elab(),
        lean_timeout(),                                        # tactic times out
        FakeStepResult(proof_status="Completed", proof_state=PS_NEW),
    ])
    a = agent_with_server(srv, tmp, "case4", script=["simp", "rfl"], max_steps=3)
    result = a.prove(THEOREM)
    events = read_events(Path(tmp) / "case4.jsonl")
    steps = [e for e in events if e["event"] == "step"]
    check("run survived the tactic timeout", result.outcome == "PROVED", result.outcome)
    check("timed-out step logged as rejected",
          steps[0]["outcome"] == "rejected", steps[0]["outcome"])
    check("step carries the timeout text",
          "did not respond in time" in steps[0]["lean_messages"][0],
          steps[0]["lean_messages"])
    check("server restarted after the tactic timeout", srv.restarts == 1, srv.restarts)
    check("next tactic ran and closed the goal",
          steps[-1]["outcome"] == "proved", steps[-1]["outcome"])

    # --------------------------------------------------------------- case 5
    print("Case 5: _ensure_server_alive is defensive")
    probe = object.__new__(LeanAgent)
    check("no server at all -> False", probe._ensure_server_alive() is False)

    srv = RecordingServer([])
    probe._server = srv
    check("live server -> True, no restart",
          probe._ensure_server_alive() is True and srv.restarts == 0, srv.restarts)

    srv.alive = False
    check("dead server -> restarted", probe._ensure_server_alive() is True)
    check("restart counted", srv.restarts == 1, srv.restarts)

    class Hopeless:
        def is_alive(self): raise RuntimeError("pipe gone")
        def restart(self): raise RuntimeError("will not come back")
    probe._server = Hopeless()
    check("unrecoverable server -> False, does not raise",
          probe._ensure_server_alive() is False)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
