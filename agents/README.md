# agents/

Harness scripts that drive Lean 4 from Python via
[LeanInteract](https://github.com/augustepoiroux/LeanInteract). Copied here from
`C:\lean\` so they live alongside the blueprint; the originals are still in place.

## Scripts

### `smoke_test.py`

Checks that the Lean harness works at all, in two parts:

1. **Core Lean `ProofStep` loop** — opens a theorem with `sorry`, then feeds
   tactics one at a time against the resulting proof state and asserts the proof
   reaches `Completed`.
2. **Mathlib reachability** — runs an `import Mathlib` command through the same
   server and closes a trivial goal with `simp`, asserting Mathlib is actually
   importable through the harness.

No API key or network needed. Prints `=== SMOKE TEST PASSED ===` on success;
fails via `assert` otherwise.

### `first_e2e_test.py`

The first end-to-end agent loop: a Claude model proposes one tactic at a time,
Lean checks it, and the result is fed back to the model. Every step is appended
to `e2e_test_log.jsonl` (and echoed to stdout) in a shape matching the pipeline
doc.

- Target theorem: `toy_comm_assoc (a b c : Nat) : a + (b + c) = c + (b + a)`,
  chosen so it is not closeable in one shot.
- The system prompt bans `ring`, `omega`, `simp`, `aesop`, `tauto`, and `decide`
  so the trace shows real step-by-step reasoning.
- Budget: `MAX_ITERATIONS = 8`; ends in `PROVED` or `FAILED_BUDGET_EXHAUSTED`.

This is a first-draft smoke test, not production harness code. The script's own
comments flag one **unverified** branch: it assumes a rejected tactic raises an
exception, which needs a real run to confirm.

## Running them

Both scripts point at a Lean project via a hardcoded path:

```python
PROJECT_DIR = r"C:\lean\MyMathlibProject"
```

They do **not** currently point at this repo — update that path if you want them
to drive `agentic` instead.

```powershell
# from the repo root, using the repo venv
.\.venv\Scripts\Activate.ps1
pip install lean-interact anthropic

python agents\smoke_test.py

# first_e2e_test.py additionally needs an API key
setx ANTHROPIC_API_KEY "sk-ant-..."   # then reopen the terminal
python agents\first_e2e_test.py
```

The first run of either script builds and caches the LeanInteract REPL binary,
so expect it to take a while before any output appears.
