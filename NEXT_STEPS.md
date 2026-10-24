# NEXT_STEPS — status as of 2026-10-24

Written from the Windows workstation (`C:\lean`). The HPC (`agentic@10.10.3.235`,
`~/agentic`) could not be reached: SSH refuses key auth and only offers password
auth, which a non-interactive session cannot supply. Everything below that
touches the HPC is therefore **prepared, not executed**.

## 1. Path fix applied (`run_experiment.py`)

Replaced the four hardcoded Windows constants with paths derived from the file's
own location, so the identical checkout runs under `C:\lean` and `~/agentic`:

```python
ROOT         = Path(__file__).resolve().parent
LOG_DIR      = ROOT / "logs"
ENV_FILE     = ROOT / ".env"
PROJECT_DIR  = ROOT / "MyMathlibProject"
CANDIDATES   = ROOT / "MyMathlibProject" / "problems" / "candidates.jsonl"
DEFAULT_OUTPUT_DIR = str(LOG_DIR)   # argparse default
```

- `main()` now `mkdir -p`s the output root (`LOG_DIR` unless `--output-dir`)
  before the per-run directory is created, so `logger.py` never writes into a
  missing folder.
- `str()` is applied where third-party code receives the project path
  (`LocalProject(directory=...)`, `BatchRunner(project_dir=...)`).
- Docstring example path changed from `C:\lean\logs\...` to `logs/...`.
- Also includes `pin_lean_toolchain()` (sets `ELAN_TOOLCHAIN` from
  `MyMathlibProject/lean-toolchain`), which fixed the 2026-10-19 Phase 1 crash
  where elan tried to download an uninstalled "stable" toolchain. On the HPC this
  is harmless: if the pinned toolchain is installed there it is simply used.
- Line endings normalised to LF (the edit had briefly introduced CRLF).

Other scripts still carry `C:\lean` constants (`run_proofs.py`, `analyze.py`,
`analysis/analyze_sycophancy.py` default candidates path, `harvest_proofs.py`,
`check_medium_parse.py`, `harness/lean_agent.py` and `harness/batch_runner.py`
*defaults*). None are on the Phase 0 path — `run_experiment.py` passes
`project_dir` explicitly — so they were left alone per the "run_experiment.py
only" scope. `analysis/analyze_sycophancy.py` is the one to fix next, since
Phase 2 analysis on the HPC will need `--candidates` passed explicitly until then.

## 2. Test results

All 16 test files pass on Windows after the change:

| file | checks |
|---|---|
| test_analyze_sycophancy | 36 |
| test_apply_tactic | 49 |
| test_batch_breaker | 19 |
| test_connectivity | 54 |
| test_get_tactic | 36 |
| test_interrogator | 29 |
| test_lean_agent | 17 |
| test_lean_error | 28 |
| test_lean_timeout | 30 |
| test_run_experiment | 37 |
| test_salvage | 34 |
| test_step_selector | 46 |
| test_tactic_extraction | 122 |
| test_transcript_logging | 70 |
| test_variant_generator | 32 |
| test_whole_proof_agent | 49 |
| **total** | **688** (0 FAIL) |

The "459" figure is the 10 pre-existing files; the 6 sycophancy-pipeline test
files add 229. Run with `PYTHONIOENCODING=utf-8 python harness/tests/<file>.py`
(standalone scripts, not pytest).

## 3. Phase 0 launch — NOT DONE (HPC unreachable)

Launch command (unchanged from the brief), to be run on the HPC after `git pull`:

```bash
cd ~/agentic && source hpc-venv/bin/activate
nohup python run_experiment.py --phase 0 --difficulty All --limit 200 --no-echo > phase0.out 2>&1 &
echo $! > phase0.pid
```

A one-shot script that pulls, re-runs the suite, and only launches if green is at
`C:\Users\HP\AppData\Local\Temp\claude\C--lean-MyMathlibProject\a3c1c7be-442c-4d7d-a0f9-2e383ea040db\scratchpad\hpc_phase0_launch.sh`
(`ssh hpc 'bash -s' < that-file`, or copy it over). It appends the PID and
launch time to this file.

Things to know before launching:

- `--difficulty All --limit 200` takes the **first 200 of 837** candidates in
  file order: 75 Easy, 94 Medium, 31 Hard. If a stratified sample was intended,
  that is not what this flag does.
- `run_experiment.py` reads `~/agentic/.env`. Phase 0 maps the provider key into
  `QWEN_API_KEY`/`QWEN_BASE_URL` for `LeanAgentHarness`; for the default
  `nvidia/*` model it needs `NVIDIA_API_KEY` (falls back to `QWEN_API_KEY`).
- The HPC needs the pinned toolchain `leanprover/lean4:v4.33.0-rc1` installed
  and `MyMathlibProject/.lake` built, otherwise the first `import Mathlib` will
  rebuild Mathlib (hours) or fail. Check with `cd MyMathlibProject && lake --version`.
- Windows reference timings from the 2026-10-19 Phase 1 run: Lean server boot
  ~12 min; first theorems ~10 min each, later ones ~30 s. Phase 0 is
  tactic-by-tactic (up to 15 LLM calls per theorem) so expect longer per theorem.

Phase 0 progress / log anomalies: **nothing to report yet — not launched.**
While it runs: `tail -20 ~/agentic/phase0.out`; results land in
`~/agentic/logs/phase0_<timestamp>/` with `summary.json` at the end.

## 4. JSONL schema doc comment ("Four event types") — NOT CHANGED

No comment in this checkout (HEAD `2a63e0e`, same as the HPC) says "four event
types" while listing five. The two candidates:

- `harness/logger.py` module docstring: lists **seven** events
  (`start | step | done | error | challenge | defense | variant`) and states no
  count.
- `FULL_PROJECT_REFERENCE.md` §"Four Event Types": heading says four and the
  table lists exactly four (`start, step, done, error`). Internally consistent,
  but stale — it predates `challenge`, `defense`, `variant`.

Changing "four" to "five" would be wrong in both places, so nothing was edited.
Recommended fix: retitle the reference section "Event Types" and add rows for
`challenge`, `defense`, `variant` from the logger docstring.

## 5. Deliberately not started

E1–E6 and the new problem set (waiting on Phase 0 results). `candidates.jsonl`
and the problem set were not modified. Nothing was pushed to GitHub; the path
fix is committed locally on `master` only.
