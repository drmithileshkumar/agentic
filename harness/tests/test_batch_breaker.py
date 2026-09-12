"""Mock tests for BatchRunner's consecutive-failure breaker.

Regression for the 20260909_002719 run: the NIM endpoint died at 16:59:24 and
the runner burned the remaining 23 theorems in 61 seconds, each failing in ~1s
with FAILED_LLM_ERROR. Those theorems were never attempted in any meaningful
sense, but their log files exist, so re-running means re-running the whole
batch. Stopping early leaves the untouched theorems usable.

This is a separate layer from the per-proof circuit breaker in lean_agent.py:
that one protects a single theorem, this one protects the batch.

Contract under test:
  streak of MAX_CONSECUTIVE_PROOF_LLM_FAILURES FAILED_LLM_ERROR -> stop
  any other outcome resets the streak, however many failures preceded it
  a stopped batch still writes its summary -- partial data is not lost
  a healthy batch runs to completion and is never truncated
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\lean")

from harness import batch_runner
from harness.batch_runner import MAX_CONSECUTIVE_PROOF_LLM_FAILURES, BatchRunner
from harness.lean_agent import ProofResult


class FakeHarness:
    """Stands in for LeanAgentHarness: returns scripted outcomes, no Lean, no API."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.attempted = 0

    def __call__(self, **kwargs):          # used as the LeanAgentHarness constructor
        return self

    def prove(self, theorem, log_path=None):
        outcome = self.outcomes[self.attempted] if self.attempted < len(self.outcomes) else "PROVED"
        self.attempted += 1
        return ProofResult(
            proof_id="p%03d" % self.attempted,
            theorem=theorem,
            outcome=outcome,
            total_steps=1,
            llm_calls=1,
            lean_calls=1 if outcome != "FAILED_LLM_ERROR" else 0,
            tactic_outcomes={},
            tactics_used=[],
        )


def run_batch(outcomes, n_theorems=None, tmpdir=None):
    """Drive BatchRunner with a scripted harness. Returns (results, fake)."""
    n = n_theorems if n_theorems is not None else len(outcomes)
    fake = FakeHarness(outcomes)
    original = batch_runner.LeanAgentHarness
    batch_runner.LeanAgentHarness = fake
    try:
        runner = BatchRunner(project_dir="unused",
                             log_dir=str(Path(tmpdir) / ("run_%d" % id(fake))),
                             echo=False)
        results = runner.run(["theorem t%d : True := by sorry" % i for i in range(n)])
    finally:
        batch_runner.LeanAgentHarness = original
    return results, fake, runner


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="harness_batch_")
    N = MAX_CONSECUTIVE_PROOF_LLM_FAILURES

    # --------------------------------------------------------------- case 1
    print("Case 1: the breaker is set to a sane threshold")
    check("threshold is 5", N == 5, N)

    # --------------------------------------------------------------- case 2
    print("Case 2: a dead endpoint stops the batch, sparing the rest")
    # 2 healthy proofs, then the endpoint dies for the remaining 48.
    outcomes = ["PROVED", "FAILED_BUDGET_EXHAUSTED"] + ["FAILED_LLM_ERROR"] * 48
    results, fake, _ = run_batch(outcomes, n_theorems=50, tmpdir=tmp)
    check("stopped after the streak, not at the end",
          fake.attempted == 2 + N, fake.attempted)
    check("results match what was attempted", len(results) == 2 + N, len(results))
    check("47 theorems left untouched (old code burned all 48)",
          50 - fake.attempted == 50 - (2 + N), 50 - fake.attempted)
    check("last result is the one that tripped it",
          results[-1].outcome == "FAILED_LLM_ERROR", results[-1].outcome)

    print("Case 2b: the aborted batch still writes its summary")
    _, _, runner = run_batch(["FAILED_LLM_ERROR"] * 20, n_theorems=20, tmpdir=tmp)
    summary = Path(runner.log_dir) / "summary.json"
    check("summary.json written despite the abort", summary.is_file(), summary)

    # --------------------------------------------------------------- case 3
    print("Case 3: the streak must be consecutive")
    # N-1 failures, one success, then N-1 failures again: never N in a row.
    outcomes = (["FAILED_LLM_ERROR"] * (N - 1) + ["PROVED"]
                + ["FAILED_LLM_ERROR"] * (N - 1) + ["PROVED"])
    results, fake, _ = run_batch(outcomes, tmpdir=tmp)
    check("ran every theorem -- no abort", fake.attempted == len(outcomes),
          fake.attempted)

    print("Case 3b: every non-LLM outcome resets the streak")
    for resetter in ("PROVED", "FAILED_BUDGET_EXHAUSTED", "FAILED_ELABORATION",
                     "FAILED_CONNECTIVITY_OUTAGE"):
        outcomes = ["FAILED_LLM_ERROR"] * (N - 1) + [resetter] + ["FAILED_LLM_ERROR"] * (N - 1)
        _, fake, _ = run_batch(outcomes, tmpdir=tmp)
        check("%s resets the streak" % resetter,
              fake.attempted == len(outcomes), fake.attempted)

    # --------------------------------------------------------------- case 4
    print("Case 4: a healthy batch is never truncated")
    outcomes = ["PROVED", "FAILED_BUDGET_EXHAUSTED"] * 10
    _, fake, _ = run_batch(outcomes, tmpdir=tmp)
    check("all 20 attempted", fake.attempted == 20, fake.attempted)

    print("Case 4b: even a batch that fails everything non-LLM runs to the end")
    outcomes = ["FAILED_BUDGET_EXHAUSTED"] * 20
    _, fake, _ = run_batch(outcomes, tmpdir=tmp)
    check("all 20 attempted", fake.attempted == 20, fake.attempted)

    # --------------------------------------------------------------- case 5
    print("Case 5: boundary -- exactly N-1 failures is not enough")
    outcomes = ["FAILED_LLM_ERROR"] * (N - 1) + ["PROVED"] * 3
    _, fake, _ = run_batch(outcomes, tmpdir=tmp)
    check("N-1 failures did not abort", fake.attempted == len(outcomes), fake.attempted)

    print("Case 5b: exactly N failures aborts, at the Nth")
    outcomes = ["FAILED_LLM_ERROR"] * N + ["PROVED"] * 3
    _, fake, _ = run_batch(outcomes, tmpdir=tmp)
    check("aborted at exactly N", fake.attempted == N, fake.attempted)

    print("Case 5c: an all-dead batch stops at N, not at the end")
    _, fake, _ = run_batch(["FAILED_LLM_ERROR"] * 50, n_theorems=50, tmpdir=tmp)
    check("stopped at N of 50", fake.attempted == N, fake.attempted)

    # --------------------------------------------------------------- case 6
    print("Case 6: the 20260909_002719 shape, replayed")
    # 24 real attempts (7 proved among them), then the endpoint dies at #25.
    real = (["PROVED"] * 3 + ["FAILED_BUDGET_EXHAUSTED"] * 9 + ["FAILED_ELABORATION"]
            + ["PROVED"] * 4 + ["FAILED_BUDGET_EXHAUSTED"] * 7)
    outcomes = real + ["FAILED_LLM_ERROR"] * 26
    results, fake, _ = run_batch(outcomes, n_theorems=50, tmpdir=tmp)
    check("all 24 real attempts survived", fake.attempted == len(real) + N,
          fake.attempted)
    check("stopped 21 theorems early (was 0)", 50 - fake.attempted == 21,
          50 - fake.attempted)
    check("the 7 proofs are still in the results",
          sum(1 for r in results if r.outcome == "PROVED") == 7,
          sum(1 for r in results if r.outcome == "PROVED"))

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
