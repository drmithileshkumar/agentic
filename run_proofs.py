"""
run_proofs.py -- entry point for batch proof runs.

Loads theorem statements from the problems manifest, wraps each one with
`by sorry`, and runs them through the harness.

Usage
-----
    # activate venv first
    .\\harness-venv\\Scripts\\Activate.ps1

    # run 50 proofs from the manifest (default)
    python run_proofs.py

    # run a specific number
    python run_proofs.py --limit 10

    # target a specific difficulty
    python run_proofs.py --difficulty Easy --limit 20

    # run a single theorem inline (for quick testing)
    python run_proofs.py --theorem "theorem foo (n : Nat) : n + 0 = n := by sorry"

Output
------
    logs/<timestamp>/proof_NNNN.jsonl   one file per proof
    logs/<timestamp>/summary.json       aggregate stats
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env from the project root so API keys don't have to be set manually
# each session. Install once: pip install python-dotenv --break-system-packages
# (or inside the venv: pip install python-dotenv)
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)  # override=False: real env vars win
        print(f"[env] Loaded {_env_file}")
except ImportError:
    pass  # python-dotenv not installed; fall back to manual env vars

PROJECT_DIR  = r"C:\lean\MyMathlibProject"
CANDIDATES   = r"C:\lean\MyMathlibProject\problems\candidates.jsonl"
LOG_BASE_DIR = r"C:\lean\logs"
# Qwen 2.5 over an OpenAI-compatible endpoint. Override per-run with --model, or
# globally with QWEN_MODEL; see harness/lean_agent.py for the env vars.
DEFAULT_MODEL      = os.environ.get("QWEN_MODEL", "qwen2.5-72b-instruct")
DEFAULT_MAX_STEPS  = 10
DEFAULT_LIMIT      = 50

# Tier codes in candidates.jsonl: E = Easy, M = Medium, H = Hard


TIER_MAP = {"Easy": "E", "Medium": "M", "Hard": "H"}


def load_theorems(
    candidates_path: str,
    limit: int,
    difficulty: str | None,
) -> list[str]:
    """
    Load theorem statements from candidates.jsonl.

    Each entry has at minimum:
        { "statement": "theorem NAME ... : TYPE", "tier": "E|M|H", ... }

    The statement field contains only the signature (no proof body), so we
    append ' := by sorry' so LeanInteract can extract the initial proof state.

    Imports are handled by the Lean project's Mathlib dependency -- statements
    that reference Mathlib types/theorems elaborate correctly inside the project.
    """
    path = Path(candidates_path)
    if not path.exists():
        print(f"ERROR: candidates.jsonl not found at {candidates_path}", file=sys.stderr)
        sys.exit(1)

    tier_filter = TIER_MAP.get(difficulty) if difficulty else None

    entries = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if tier_filter and e.get("tier") != tier_filter:
                continue
            entries.append(e)

    entries = entries[:limit]

    theorems = []
    for e in entries:
        stmt = e.get("statement", "").strip()
        if not stmt:
            continue
        # Add `import Mathlib` header + sorry body so lean_interact gets a proof state
        full = f"import Mathlib\n\n{stmt} := by sorry"
        theorems.append(full)

    print(f"Loaded {len(theorems)} theorems from candidates.jsonl"
          + (f" (tier={tier_filter})" if tier_filter else ""))
    return theorems


def main():
    parser = argparse.ArgumentParser(description="Batch proof runner")
    parser.add_argument("--limit",      type=int,  default=DEFAULT_LIMIT,
                        help="Max theorems to attempt (default 50)")
    parser.add_argument("--difficulty", type=str,  default=None,
                        choices=["Easy", "Medium", "Hard"],
                        help="Filter by difficulty (E/M/H in candidates.jsonl)")
    parser.add_argument("--model",      type=str,  default=DEFAULT_MODEL,
                        help="Qwen model name (must match QWEN_BASE_URL's provider)")
    parser.add_argument("--max-steps",  type=int,  default=DEFAULT_MAX_STEPS,
                        help="Tactic budget per proof")
    parser.add_argument("--theorem",    type=str,  default=None,
                        help="Run a single theorem string instead of the manifest")
    parser.add_argument("--no-echo",    action="store_true",
                        help="Suppress per-step stdout output")
    args = parser.parse_args()

    # timestamped log directory so runs don't overwrite each other
    run_ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = str(Path(LOG_BASE_DIR) / run_ts)

    # import here so startup errors are reported cleanly
    from harness import BatchRunner

    runner = BatchRunner(
        project_dir = PROJECT_DIR,
        log_dir     = log_dir,
        model       = args.model,
        max_steps   = args.max_steps,
        echo        = not args.no_echo,
    )

    if args.theorem:
        theorems = [args.theorem]
    else:
        theorems = load_theorems(CANDIDATES, args.limit, args.difficulty)

    if not theorems:
        print("No theorems to run. Check manifest path / difficulty filter.", file=sys.stderr)
        sys.exit(1)

    results = runner.run(theorems)

    proved = sum(1 for r in results if r.proved)
    print(f"\nDone. {proved}/{len(results)} proved. Logs at {log_dir}/")
    return 0 if proved > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
