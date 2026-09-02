"""
BatchRunner -- run many theorems through LeanAgentHarness and collect stats.

Usage
-----
    from harness import BatchRunner

    runner = BatchRunner(
        project_dir = r"C:\\lean\\MyMathlibProject",
        log_dir     = "logs/batch_001/",
        model       = "qwen2.5-72b-instruct",
        max_steps   = 10,
    )
    results = runner.run(theorems)   # list[str]
    runner.print_summary(results)
    runner.save_summary(results)     # writes logs/batch_001/summary.json
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .lean_agent import DEFAULT_MODEL, LeanAgentHarness, ProofResult


class BatchRunner:
    """
    Runs a list of theorem strings through the harness one at a time,
    collecting per-proof and aggregate stats.

    Each proof gets its own JSONL log inside *log_dir*:
        logs/batch_001/proof_0001.jsonl
        logs/batch_001/proof_0002.jsonl
        ...
        logs/batch_001/summary.json

    Parameters
    ----------
    project_dir : str
    log_dir     : str   Directory for all output files.
    model       : str   Qwen model name.
    max_steps   : int   Per-proof tactic budget.
    echo        : bool  Print each step to stdout.
    """

    def __init__(
        self,
        project_dir: str,
        log_dir:     str  = "logs/batch/",
        model:       str  = DEFAULT_MODEL,
        max_steps:   int  = 10,
        echo:        bool = True,
    ):
        self.project_dir = project_dir
        self.log_dir     = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.model       = model
        self.max_steps   = max_steps
        self.echo        = echo

    # ------------------------------------------------------------------

    def run(self, theorems: list[str]) -> list[ProofResult]:
        """
        Attempt each theorem in *theorems* in order.

        A single LeanServer is built up front and reused across all proofs, so
        the multi-minute `import Mathlib` is paid once per batch rather than
        once per theorem. Each proof still gets its own JSONL file.

        Returns a list of ProofResult objects in the same order.
        """
        total = len(theorems)
        print(f"\n{'='*60}")
        print(f"BatchRunner: {total} theorems, model={self.model}, max_steps={self.max_steps}")
        print(f"Logs: {self.log_dir}")
        print(f"{'='*60}\n")

        results: list[ProofResult] = []

        # Build the harness -- and with it the LeanServer -- exactly once. The
        # server's first command pays for `import Mathlib`, which takes minutes;
        # constructing one per theorem would re-pay that on every proof.
        print("Starting Lean server (first `import Mathlib` takes several minutes)...")
        t_boot  = time.perf_counter()
        harness = LeanAgentHarness(
            project_dir = self.project_dir,
            log_path    = str(self.log_dir / "proof_0001.jsonl"),
            model       = self.model,
            max_steps   = self.max_steps,
            echo        = self.echo,
        )
        print(f"Lean server ready in {time.perf_counter() - t_boot:.1f}s\n")

        for idx, theorem in enumerate(theorems, start=1):
            log_path = self.log_dir / f"proof_{idx:04d}.jsonl"
            print(f"\n[{idx}/{total}] Starting proof {idx:04d}")
            print(f"  theorem: {theorem[:80]}{'...' if len(theorem) > 80 else ''}")

            t0     = time.perf_counter()
            result = harness.prove(theorem, log_path=str(log_path))
            elapsed = time.perf_counter() - t0

            results.append(result)
            status = "[PROVED]" if result.proved else f"[FAILED: {result.outcome}]"
            print(f"  [{status}] steps={result.total_steps}  "
                  f"llm={result.llm_calls}  lean={result.lean_calls}  "
                  f"time={elapsed:.1f}s")

        self.print_summary(results)
        self.save_summary(results)
        return results

    # ------------------------------------------------------------------

    def print_summary(self, results: list[ProofResult]) -> None:
        total   = len(results)
        proved  = sum(1 for r in results if r.proved)
        failed  = total - proved

        # aggregate tactic outcome counts across all proofs
        all_outcomes: dict[str, int] = defaultdict(int)
        for r in results:
            for outcome, count in r.tactic_outcomes.items():
                all_outcomes[outcome] += count

        avg_steps      = sum(r.total_steps for r in results) / total if total else 0
        avg_lean_norm  = sum(r.lean_calls_per_step for r in results) / total if total else 0
        avg_llm_norm   = sum(r.llm_calls_per_step  for r in results) / total if total else 0

        print(f"\n{'='*60}")
        print(f"BATCH SUMMARY -- {total} theorems")
        print(f"{'='*60}")
        print(f"  Proved:                {proved}/{total} ({100*proved//total if total else 0}%)")
        print(f"  Failed:                {failed}")
        print(f"  Avg steps/proof:       {avg_steps:.1f}")
        print(f"  Avg lean calls/step:   {avg_lean_norm:.2f}")
        print(f"  Avg llm  calls/step:   {avg_llm_norm:.2f}")
        print(f"\n  Tactic outcomes (all proofs):")
        for outcome, count in sorted(all_outcomes.items(), key=lambda x: -x[1]):
            print(f"    {outcome:<20} {count}")
        print(f"{'='*60}\n")

    def save_summary(self, results: list[ProofResult]) -> Path:
        """Write a machine-readable summary.json to log_dir."""
        total  = len(results)
        proved = sum(1 for r in results if r.proved)

        all_outcomes: dict[str, int] = defaultdict(int)
        for r in results:
            for outcome, count in r.tactic_outcomes.items():
                all_outcomes[outcome] += count

        summary = {
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "model":              self.model,
            "max_steps":          self.max_steps,
            "total_theorems":     total,
            "proved":             proved,
            "failed":             total - proved,
            "pass_rate":          proved / total if total else 0,
            "avg_steps":          sum(r.total_steps for r in results) / total if total else 0,
            "avg_lean_per_step":  sum(r.lean_calls_per_step for r in results) / total if total else 0,
            "avg_llm_per_step":   sum(r.llm_calls_per_step  for r in results) / total if total else 0,
            "tactic_outcomes":    dict(all_outcomes),
            "per_proof": [
                {
                    "proof_id":        r.proof_id,
                    "theorem":         r.theorem[:120],
                    "outcome":         r.outcome,
                    "total_steps":     r.total_steps,
                    "llm_calls":       r.llm_calls,
                    "lean_calls":      r.lean_calls,
                    "lean_per_step":   r.lean_calls_per_step,
                    "llm_per_step":    r.llm_calls_per_step,
                    "tactic_outcomes": r.tactic_outcomes,
                }
                for r in results
            ],
        }

        out = self.log_dir / "summary.json"
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Summary written to {out}")
        return out
