# Agentic Theorem Proving with Lean 4
## Complete Project Reference — Independent Study
### anshhika · September 2026

---

# PART 1: EXECUTIVE SUMMARY

This independent study investigates how far a state-of-the-art frontier LLM — used as a greedy, one-tactic-at-a-time agent with no fine-tuning and no tree search — can get on real Mathlib theorem proving tasks. The model is NVIDIA Nemotron-3-Ultra-550B (550 billion parameters). Every agent↔Lean exchange is logged in structured JSONL format; the result is both a research study and a reproducible benchmark.

The research fills a specific gap: while SOTA systems (Seed-Prover: 99.6% miniF2F, BFS-Prover V2: 95.08%) rely on RL/fine-tuning/tree search, nobody has rigorously characterised what a raw frontier LLM achieves on real-world Mathlib sorries with exhaustive failure analysis.

**Core output:** A reproducible benchmark — 837 Mathlib problems, an open-source harness, and the first published failure-mode taxonomy for LLM-based Lean agents.

**Target venues:** AITP, ICLR/NeurIPS Math-AI workshop, COLM.

---

# PART 2: CURRENT STATUS (Sep 9, 2026)

## What's Done
- Full harness built and verified: lean_agent.py, logger.py, batch_runner.py, run_proofs.py
- Empty tactic retry fix (LLM returns "" → up to 3 retries with nudge message)
- Real llm_calls counting (retries counted, exception-tagged via _tag_call_count)
- Real lean_calls counting (rejected path = 0 lean calls, not 1)
- **Complete verbatim transcript logging** — every prompt sent, every raw completion before extraction, every retry and why it was discarded, and the exact history the model saw at each step
- **Tactic extractor** (`_extract_tactic`) — pulls the tactic out of reasoning-model prose
- **LeanError crash fixed** — lean_interact returns either a ProofStepResponse or a `LeanError` (which carries `.message`, not `.messages`); reading the latter as the former killed a whole batch
- **Split circuit breaker** — connectivity failures retry with backoff; model failures still fail fast
- **Per-call timeouts** on both the LLM call and the Lean REPL
- **367 mock tests passing across 7 test files** in `harness/tests/` — no network, no API key, no Lean server required
- Analysis scripts written: `analyze.py`, `classify_failures.py`, `history_analysis.py`, `build_dashboard_data.py`
- Dashboard built: `results_dashboard.html` (Chart.js, theme-aware, reads `dashboard_data.json`)
- ASCII patch (no Unicode crashes on cp1252 terminals — "[PROVED]" not "✓ PROVED")
- .env auto-loading via python-dotenv
- NVIDIA NIM API key set in C:\lean\.env
- Lean cache warm (8,279 .olean files, toolchain 4.33.1 installed)
- Research plan doc: C:\lean\AgenticTheoremProving_ResearchPlan.docx

## Runs So Far

| Run directory | What | Outcome |
|---------------|------|---------|
| 20260906_164928 | Smoke — toy_comm | PROVED in 2 steps. First end-to-end Nemotron proof. |
| 20260906_171651 | **Easy-5** | **5/5 proved.** Steps: 1, 1, 1, 4, 3. First real data. |
| 20260906_183606 | Easy-50 attempt | Killed by harness task lifecycle at proof 2. 1 proof completed. |
| 20260906_191021 | **Easy-50 run 1** | **Aborted at 32/50.** Compromised — see below. |
| 20260908_183715 | Easy-50 run 2 | **Crashed on theorem 1 of 50**, 0 proof files. Lean timeout regression, since fixed. |
| 20260909_002719 | **Easy-50 run 3** | **IN PROGRESS.** Wrapper PID 35580 → python 37724 → Lean worker 4784. |

### Why Easy-50 run 1 is not usable
Of 31 completed proofs: 3 PROVED, 19 FAILED_LLM_ERROR, 6 FAILED_BUDGET_EXHAUSTED, 3 FAILED_ELABORATION. The 19 LLM errors were **not model failures** — 55 logged `APIConnectionError: Connection error.` from a NIM outage, with 12 theorems destroyed inside a single 40-second window. Separately, proof_0024 sat inside one un-timed-out socket call for **22.3 hours** doing zero work. The headline 3/31 = 9.7% is meaningless; the defensible slice is **3/9 = 33% on theorems that actually reached a working model** (95% CI [12.1%, 64.6%], n far too small to claim anything). Both root causes are fixed (split circuit breaker, per-call timeouts).

## Immediate Next
1. Easy-50 run 3 results → `analyze.py`, `classify_failures.py`, `history_analysis.py`, dashboard
2. Launch Medium-50
3. Launch Hard-50
4. Draft paper sections 1–5

---

# PART 3: STACK & INFRASTRUCTURE

## Technology
- **Lean 4** + Mathlib v4.33.0-rc1
- **lean_interact** library (LeanServer, ProofStep, Command, LocalProject, LeanREPLConfig)
- **LLM:** NVIDIA Nemotron-3-Ultra-550B via NVIDIA NIM API (OpenAI-compatible)
- **API base URL:** https://integrate.api.nvidia.com/v1
- **Model name:** nvidia/nemotron-3-ultra-550b-a55b
- **Credentials:** C:\lean\.env (auto-loaded by run_proofs.py via python-dotenv)
- **Problem set:** C:\lean\MyMathlibProject\problems\candidates.jsonl — 837 problems, tiers E/M/H (310/378/149)
- **Lean project:** C:\lean\MyMathlibProject
- **Python venv:** C:\lean\harness-venv (Python 3.13.5)

## File Structure
```
C:\lean\
├── .env                          ← API credentials (never commit)
├── run_proofs.py                 ← CLI entry point
├── analyze.py                    ← pass rate + step economy + tactic distribution (RQ1/3/5)
├── classify_failures.py          ← failure mode taxonomy (RQ2)
├── history_analysis.py           ← repetition, advanced-rate, goal shrinkage (RQ4)
├── build_dashboard_data.py       ← merges all three into dashboard_data.json
├── results_dashboard.html        ← professor demo (Chart.js, theme-aware)
├── AgenticTheoremProving_ResearchPlan.docx
├── FULL_PROJECT_REFERENCE.md     ← this file
├── harness/
│   ├── __init__.py
│   ├── lean_agent.py             ← LeanAgentHarness: proof loop, LLM calls, extraction
│   ├── logger.py                 ← ProofLogger: JSONL event logging
│   ├── batch_runner.py           ← BatchRunner: runs many proofs, shared LeanServer
│   └── tests/                    ← 367 mock tests, 7 files, no network/Lean needed
│       ├── test_apply_tactic.py       (30)  outcome classification, lean_calls accounting
│       ├── test_connectivity.py       (51)  split circuit breaker, backoff, timeouts
│       ├── test_get_tactic.py         (36)  empty-completion retry, llm_calls accounting
│       ├── test_lean_error.py         (28)  LeanError handling at tactic + elaboration
│       ├── test_lean_timeout.py       (30)  Lean budgets, containment, REPL restart
│       ├── test_tactic_extraction.py (122)  _extract_tactic rules and invariants
│       └── test_transcript_logging.py (70)  verbatim JSONL transcript fields
├── logs/
│   └── <timestamp>/
│       ├── proof_0001.jsonl      ← per-proof event log
│       ├── summary.json          ← aggregate stats
│       └── dashboard_data.json   ← merged analysis feed (built on demand)
└── MyMathlibProject/
    ├── problems/candidates.jsonl
    └── .lake/                    ← Lean build cache (WARM — 8,279 .olean files)
```

## How to Run
```powershell
# Credentials auto-load from C:\lean\.env
cd C:\lean
.\harness-venv\Scripts\Activate.ps1

# Smoke test (1 proof)
python -u run_proofs.py --theorem "import Mathlib`n`ntheorem toy_comm (a b : Nat) : a + b = b + a := by sorry" --max-steps 6

# 5 Easy proofs
python -u run_proofs.py --difficulty Easy --limit 5 --max-steps 15

# 50 Easy proofs (overnight) — MUST be launched detached, see PART 8
Start-Process powershell -ArgumentList "-Command", "cd C:\lean; .\harness-venv\Scripts\Activate.ps1; python -u run_proofs.py --difficulty Easy --limit 50 --max-steps 15 --no-echo" -RedirectStandardOutput "C:\lean\logs\easy50_run.txt" -RedirectStandardError "C:\lean\logs\easy50_run.err.txt" -WindowStyle Hidden

# 50 Medium / Hard
python -u run_proofs.py --difficulty Medium --limit 50 --max-steps 15 --no-echo
python -u run_proofs.py --difficulty Hard --limit 50 --max-steps 15 --no-echo

# Analysis
python analyze.py --logs-dir C:\lean\logs\<timestamp>
python classify_failures.py --logs-dir C:\lean\logs\<timestamp>
python history_analysis.py --logs-dir C:\lean\logs\<timestamp>

# Dashboard (fetch() is blocked on file:// — must be served over HTTP)
python build_dashboard_data.py --logs-dir C:\lean\logs\<timestamp> --out C:\lean\dashboard_data.json
python -m http.server 8000
# http://localhost:8000/results_dashboard.html

# Tests
for %f in (harness\tests\test_*.py) do python %f
```

---

# PART 4: SYSTEM ARCHITECTURE

## Component Map
```
candidates.jsonl (837 theorems)
         |
         v
run_proofs.py -- CLI: --difficulty, --limit, --max-steps, --model
         |
         v
BatchRunner -- builds ONE LeanServer, loops over theorems
         |
    +----+---------------------------+
    |                                |
LeanAgentHarness               ProofLogger
    |                                |
    +-- Lean side:                   |  <- JSONL event per step:
    |    LeanServer                  |    { event, tactic, outcome,
    |    Command(stmt)   t=900s      |      goal_before, goal_after,
    |    ProofStep(t, ps) t=120s     |      llm_calls, lean_calls,
    |    LeanError handled           |      lean_messages, llm_input,
    |    killed REPL restarted       |      llm_raw_response,
    |                                |      retry_attempts,
    +-- LLM side:                    |      history_passed, timestamp }
    |    Nemotron-3-Ultra-550B
    |    NVIDIA NIM API
    |    OpenAI-compatible
    |    MAX_TOKENS=8192, timeout=600s
    |
    +-- _extract_tactic()  <- prose -> tactic, between LLM and Lean
    |
    +-- Split circuit breaker:
         model failures        3 strikes -> FAILED_LLM_ERROR
         connectivity failures backoff 30/60/120/240/300,
                               10 strikes -> FAILED_CONNECTIVITY_OUTAGE

Output per run:
    C:\lean\logs\<timestamp>\proof_0001.jsonl   <- per-proof event log
    C:\lean\logs\<timestamp>\proof_0002.jsonl
    ...
    C:\lean\logs\<timestamp>\summary.json       <- aggregate stats
```

## Proof Loop (Step by Step)

| Phase | What Happens | Logged As |
|-------|-------------|-----------|
| 1. Elaboration | LeanServer receives Command(cmd=full_theorem) with `timeout=900s` — the command carries its own `import Mathlib` and takes ~230s. Lean returns the sorry proof state. Failure is contained: one bad theorem costs one theorem. | event=start, goal, lean_raw_elaboration |
| 2. LLM Call | System prompt + current goal + full tactic history → Nemotron-550B, `max_tokens=8192`, `timeout=600s`. Up to 3 retries if the completion is empty. | event=step, llm_calls=N, llm_input, retry_attempts |
| 3. Extraction | `_extract_tactic()` scans the raw completion bottom-up for the last tactic-looking line, strips code fences and inline backticks. Only the extracted string reaches Lean; the raw completion is still logged. | event=step, llm_raw_response (raw), tactic (extracted) |
| 4. Lean Check | ProofStep(tactic=t, proof_state=ps) sent to LeanServer with `timeout=120s`. Returns new proof_state, messages, proof_status — or a `LeanError`. | event=step, lean_calls=1 |
| 5. Classify | proof_status=="Completed" → proved. LeanError or error messages → error. Unchanged → no_progress. Else → advanced. Unreachable endpoint → connectivity_error. | event=step, outcome |
| 6. History | Step number, tactic, outcome and Lean's message appended to the history list. Passed verbatim to the LLM next step, and logged as `history_passed`. | event=step, history_passed |
| 7. Terminate | proved → PROVED. step==max_steps → FAILED_BUDGET_EXHAUSTED. 3 model failures → FAILED_LLM_ERROR. 10 connectivity failures → FAILED_CONNECTIVITY_OUTAGE. Elaboration failure → FAILED_ELABORATION. | event=done, result |

## Failure Budgets

Two things can go wrong with an LLM call, and conflating them is expensive — in run 1 a ~40-second outage destroyed 12 consecutive theorems. They are now counted separately:

| Kind | Trigger | Behaviour | Limit | Result code |
|------|---------|-----------|-------|-------------|
| **Model failure** | Endpoint answered with nothing usable: empty completion after retries, 404, 401, bad model name | Fail fast — retrying will not help | 3 consecutive | FAILED_LLM_ERROR |
| **Connectivity failure** | `APIConnectionError` (incl. `APITimeoutError`), `InternalServerError`, `httpx.TransportError`/`TimeoutException`, any 5xx, `OSError` | Log `connectivity_error`, back off 30 → 60 → 120 → 240 → 300s (capped), retry | 10 consecutive (~32 min) | FAILED_CONNECTIVITY_OUTAGE |

Only a **successful** call resets a counter — a model failure does not hand the connectivity budget a free reset, or alternating error kinds would keep a dead endpoint alive forever.

## Timeouts

| Constant | Value | Why |
|----------|-------|-----|
| `LLM_CALL_TIMEOUT_SECONDS` | 600 | Run 1 proof_0024 hung 22.3 hours inside one un-timed-out call. 15 steps × 600s bounds a proof at 2.5h. |
| `LEAN_ELABORATION_TIMEOUT_SECONDS` | 900 | The elaboration Command re-imports Mathlib *inside the command* — measured at ~230s. |
| `LEAN_TACTIC_TIMEOUT_SECONDS` | 120 | A tactic needing longer is stuck in an unbounded search. |

`lean_interact` calls `kill()` on the server when a command times out, so `_ensure_server_alive()` restarts the REPL afterwards — otherwise every later theorem faces a dead server that costs ~10 minutes to rebuild.

## System Prompt (sent to LLM every step)
```
You are proving a Lean 4 theorem one tactic at a time.
You will be shown the current goal state and a history of what you tried.
Respond with EXACTLY ONE Lean 4 tactic and nothing else — no explanation,
no code fences, no markdown. Just the tactic text, e.g.:
    rw [Nat.add_comm]

If the previous step returned an error, read it carefully and adjust.
If the goal did not change, your tactic had no effect — try something different.
```

---

# PART 5: WHAT WE DOCUMENT — JSONL SCHEMA

## Four Event Types

| Event | When | Key Fields | Research Use |
|-------|------|-----------|--------------|
| start | After Lean elaborates the theorem | proof_id, theorem, goal, lean_raw_elaboration | Initial proof state + Lean's verbatim response |
| step | After each tactic attempt | step, tactic, outcome, goal_before, goal_after, lean_messages, llm_calls, lean_calls, llm_input, llm_raw_response, retry_attempts, history_passed | Full agent↔Lean exchange, verbatim both directions |
| done | When proof ends | result, total_steps, tactic_outcomes, tactics_used, lean_calls_per_step, llm_calls_per_step | Summary for aggregate analysis |
| error | On elaboration failure | detail, raw | Dataset quality |

## Full step Event Schema
```json
{
  "proof_id": "uuid4 — links all events for one proof",
  "theorem": "import Mathlib\n\ntheorem ...",
  "event": "step",
  "step": 1,
  "tactic": "exact Nat.add_comm _ _",
  "outcome": "proved | advanced | no_progress | error | rejected | connectivity_error",
  "goal_before": "a b : N\n|- a + b = b + a",
  "goal_after": null,
  "lean_messages": [],
  "llm_calls": 1,
  "lean_calls": 1,

  "llm_input": {
    "system": "You are proving a Lean 4 theorem one tactic at a time. ...",
    "user": "Current goal:\n...\n\nHistory:\n...\n\nPropose the next tactic."
  },
  "llm_raw_response": "verbatim completion, BEFORE extraction or stripping",
  "retry_attempts": [
    {"attempt": 1, "raw_response": "", "extracted_tactic": "",
     "reason_skipped": "empty_tactic"},
    {"attempt": 2, "raw_response": "simp", "extracted_tactic": "simp",
     "accepted": true}
  ],
  "history_passed": [
    {"step": 1, "tactic": "simp", "outcome": "error",
     "message": "Lean error: unknown identifier"}
  ],

  "timestamp": "2026-09-02T04:01:12Z"
}
```

**Why these four fields exist:** without them a log records what the harness *decided*, not what was *said*. `llm_raw_response` vs `tactic` is what separates a model failure from an extraction failure. `retry_attempts` shows every completion parsed and why each was discarded. `history_passed` is the exact list the model saw at that step — logged before the step appends its own entry, so it is a true per-step snapshot, never back-filled.

## Full start Event Schema
```json
{
  "proof_id": "uuid4",
  "theorem": "import Mathlib\n\ntheorem ...",
  "event": "start",
  "goal": "a b : ℕ\n⊢ a + b = b + a",
  "lean_raw_elaboration": {
    "messages": [{"start_pos": {"line": 3, "column": 8},
                  "end_pos": {"line": 3, "column": 16},
                  "severity": "warning", "data": "declaration uses `sorry`"}],
    "sorries": [{"start_pos": {"line": 3, "column": 51},
                 "end_pos": {"line": 3, "column": 56},
                 "goal": "a b : ℕ\n⊢ a + b = b + a", "proof_state": 0}],
    "env": 0, "tactics": [], "declarations": [], "infotree": null
  },
  "timestamp": "2026-09-06T11:32:05Z"
}
```
`lean_raw_elaboration` is a `model_dump()` of the pydantic Command response, round-tripped through JSON. If it is not serialisable it degrades to `str()`, and if that fails it is written as `null` — it never crashes the run.

## Outcome Taxonomy

| Outcome | Lean Condition | lean_calls | Research Meaning |
|---------|---------------|-----------|-----------------|
| proved | proof_status == Completed | 1 | SUCCESS |
| advanced | proof_state changed, no errors | 1 | Real progress |
| no_progress | proof_state unchanged, no errors | 1 | Model stuck |
| error | Lean returned error messages, or a LeanError, or a model-side API failure | 1 (0 if never reached Lean) | Type/parse error |
| rejected | lean_interact raised an exception (incl. tactic timeout) | 0 or 1 | Empty tactic or protocol failure |
| connectivity_error | Endpoint unreachable; step retried after backoff | 0 | Infrastructure, **not** a model result — exclude from capability metrics |

## Proof-Level Result Codes

| Code | Meaning |
|------|---------|
| PROVED | Proof closed within budget |
| FAILED_BUDGET_EXHAUSTED | Hit max_steps without proving |
| FAILED_ELABORATION | Lean rejected the theorem statement itself, or elaboration timed out |
| FAILED_LLM_ERROR | 3+ consecutive model failures (empty completions, 404, auth) |
| FAILED_CONNECTIVITY_OUTAGE | 10+ consecutive connectivity failures (~32 min of outage) |

**Analysis note:** FAILED_CONNECTIVITY_OUTAGE and FAILED_ELABORATION are *not* model failures. Pass rate must be computed over theorems that actually reached a working model, or an outage silently deflates the headline number — exactly what happened in Easy-50 run 1.

---

# PART 6: RESEARCH QUESTIONS (DETAILED)

## RQ1 — Capability Ceiling

**Question:** What pass rate does Nemotron-3-Ultra-550B achieve on Mathlib Easy / Medium / Hard?

**Rationale:** Establishes the baseline all future work references. Also tests whether E/M/H tier labels are calibrated to agent difficulty (not just human difficulty) — a dataset contribution.

**Method:** Run 50 Easy, 50 Medium, 50 Hard proofs (150 total). Record pass rate per tier with 95% Wilson confidence intervals. Fisher's exact test: is Easy significantly higher than Medium? Compare vs SorryDB published baselines.

**Expected:** Easy 15–35%, Medium 3–12%, Hard 0–3%. If Medium ≈ Easy, tier labels are not agent-meaningful — itself a finding.

---

## RQ2 — Failure Mode Taxonomy

**Question:** When the agent fails, WHY? Can we classify into a mutually exclusive, exhaustive taxonomy?

**Rationale:** The most original contribution. No prior paper has published a fine-grained taxonomy of HOW LLM agents fail on formal proofs. This directly tells us which improvement — better prompting, search, tool use, fine-tuning — would help most.

**Five Failure Modes:**

| Mode | Detection Rule | Implication |
|------|---------------|-------------|
| No-Progress Loop | Last k steps (k≥3) all outcome==no_progress | Model stuck. Needs diversity or backtracking. |
| Error Loop | Same error in lean_messages on 3+ consecutive steps | Model doesn't update from Lean feedback. |
| Hallucinated Lemma | "unknown identifier" + capital-letter name in tactic | Model invents Mathlib lemma names. |
| Syntax Error Loop | "expected"/"parse error" in lean_messages 3+ steps | Right strategy, wrong Lean syntax. |
| Budget Exhaustion (mixed) | None of the above dominate | Made some progress but ran out of steps. |

**Classifier pseudocode:**
```python
def classify_failure(steps):
    last_3 = steps[-3:]
    if all(s["outcome"] == "no_progress" for s in last_3):
        return "NO_PROGRESS_LOOP"
    errors = [s["lean_messages"][0] for s in steps if s["lean_messages"]]
    if len(errors) >= 3 and len(set(errors[-3:])) == 1:
        return "ERROR_LOOP"
    for s in steps:
        msgs = " ".join(s["lean_messages"])
        if "unknown identifier" in msgs:
            if any(w[0].isupper() for w in s["tactic"].split()):
                return "HALLUCINATED_LEMMA"
    parse_errors = [s for s in steps if any(k in " ".join(s["lean_messages"])
                    for k in ["expected", "parse error"])]
    if len(parse_errors) >= 3:
        return "SYNTAX_ERROR_LOOP"
    return "BUDGET_EXHAUSTION_MIXED"
```

The shipped `classify_failures.py` refines this: capitalised names in the Lean/Mathlib stdlib (`Nat`, `Int`, `Real`, `List`, `Set`, `Finset`, `Bool`, `Prop`, `Type`, `Sort`) are whitelisted, so `exact Nat.add_comm _ _` is not mistaken for a hallucination.

**Expected:** No-Progress Loop ~40%, Hallucinated Lemma ~25–30%, Syntax ~10%, Error Loop ~15%, Mixed ~10%.

**Caution learned from run 1:** repeated `APIConnectionError` messages will classify as ERROR_LOOP if connectivity failures are left in the denominator. Filter by result code before reading the taxonomy.

---

## RQ3 — Step Economy

**Question:** How many steps does proving a theorem require? Is there a point of no return?

**Method:** Histogram of total_steps for PROVED proofs. Survival analysis: P(proved | still alive after k steps). Scatter: theorem length vs. steps.

**Expected:** Proved proofs close in median 3–5 steps. Survival curve drops sharply after step 8. This motivates early stopping: if no progress by step 8, abort.

---

## RQ4 — History Utilisation

**Question:** Does the model actually use its tactic history to improve?

**Method (no new runs needed — pure JSONL analysis):**
- Repetition rate: fraction of steps where tactic was already tried
- Advanced-rate trend: mean(outcome==advanced) by step number — does it increase?
- Goal shrinkage: character count of goal_before at each step

`history_passed` makes this exact rather than reconstructed — the log records the literal list the model was shown at each step.

**Expected:** Repetition rate > 20%. Advanced rate does NOT increase with step. Model underutilises history — motivates structured history summarisation.

---

## RQ5 — Tactic Distribution

**Question:** Which tactics does the model use? Which ones work?

**Method:**
- Frequency table: all tactics used, sorted by count
- Precision: (times as final step in PROVED proof) / (total uses)
- Two-curve chart: usage frequency vs. success rate by tactic rank
- Cluster: reliable closers (omega, decide, rfl), progress makers (rw, apply, intro), dead ends (repeated simp)

**Expected:** simp or rw most-used. omega/rfl highest precision. Big gap between usage and success for simp.

---

# PART 7: DATASET

## candidates.jsonl

| Property | Value |
|----------|-------|
| Path | C:\lean\MyMathlibProject\problems\candidates.jsonl |
| Total | 837 problems |
| Easy (E) | 310 (37%) |
| Medium (M) | 378 (45%) |
| Hard (H) | 149 (18%) |
| Format | JSONL — one JSON object per line |
| Required fields | "statement": theorem signature, "tier": E/M/H |
| Source | Curated from real Mathlib — not competition problems |

**How theorems are prepared:**
```
import Mathlib

{statement} := by sorry
```
The "by sorry" placeholder lets lean_interact extract the initial proof state.

**Note on tier metadata:** the `tier` field lives in candidates.jsonl but is **not** carried into the JSONL start event. `analyze.py` infers it from the theorem identifier pattern (`ALG_E_01`, `SET_M_03`, `NUM_H_02`) and labels the breakdown as inferred. Theorems named in other conventions (`exercise_2_1_5`, `exercise_11_4_6b`) have no inferable tier — 22 of 31 in run 1.

---

# PART 8: EXPERIMENTAL DESIGN

## Model Config

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | nvidia/nemotron-3-ultra-550b-a55b | Largest freely accessible frontier model |
| API | NVIDIA NIM (integrate.api.nvidia.com/v1) | Free tier, OpenAI-compatible |
| max_tokens | **8192** per call | Nemotron is a *reasoning* model: it emits ~1700+ reasoning tokens on set-theory goals before committing to a tactic. Raised 150 → 2048 → 8192; 2048 still truncated mid-reasoning. |
| LLM call timeout | 600s | Bounds a 15-step proof at 2.5h; prevents socket hangs |
| max_steps | 15 per proof | Generous budget after bug fix |
| Temperature | Provider default | Baseline; future: compare T=0 vs T=0.7 |

## Run Protocol

| Run | Command | Purpose |
|-----|---------|---------|
| Smoke | --theorem "toy_comm" --max-steps 6 | Verify Nemotron + empty tactic fix |
| Easy-5 | --difficulty Easy --limit 5 --max-steps 15 | First real data |
| Easy-50 | --difficulty Easy --limit 50 --max-steps 15 --no-echo | Main Easy result |
| Medium-50 | --difficulty Medium --limit 50 --max-steps 15 --no-echo | Tier comparison |
| Hard-50 | --difficulty Hard --limit 50 --max-steps 15 --no-echo | Completes tier comparison |

## Pre-Run Checklist
- [ ] Empty tactic retry fix in lean_agent.py
- [ ] C:\lean\.env has real NVIDIA key (not placeholder)
- [ ] python-dotenv installed in harness-venv
- [ ] Lean cache warm (.lake/packages/mathlib/.lake/build/lib has 8,279+ .olean files)
- [ ] Smoke test passes: at least one step event logged with non-empty tactic
- [ ] **All tests green before a long run** — `for %f in (harness\tests\test_*.py) do python %f`, expect 367 passing
- [ ] **Constants live in the code the run will import** — `python -c "from harness import lean_agent as L; print(L.MAX_TOKENS, L.LLM_CALL_TIMEOUT_SECONDS)"`
- [ ] **Endpoint reachable right now** — a NIM outage at launch costs the whole batch
- [ ] **Launch detached via `Start-Process`** — a run launched as a child of a tool/agent background task dies when that task is reaped (lost the 20260906_183606 run at proof 2)
- [ ] **Record the PID** — wrapper PID, python PID, Lean worker PID; kill the wrapper to stop cleanly
- [ ] **stderr file is empty after boot** — the 20260908_183715 crash was visible only in stderr; stdout looked healthy
- [ ] **Watch past the first elaboration (~230s after "Lean server ready")** before walking away — a timeout regression only shows up on theorem 1
- [ ] **Confirm 2–3 completed proofs** before leaving a multi-hour run unattended

---

# PART 9: ANALYSIS PLAN

## Pass Rate (RQ1)
- Metric: proved/total per tier. 95% Wilson CI (implemented directly — no scipy).
- Test: Fisher's exact test Easy vs Medium, Medium vs Hard. p-value + odds ratio.
- Chart: grouped bar chart per tier with CI error bars.
- Comparison: vs SorryDB agentic baselines (~20–30%).
- **Exclude infrastructure failures** (FAILED_CONNECTIVITY_OUTAGE) and incomplete proofs from the denominator.

## Failure Mode (RQ2)
- Run classify_failures.py on all failed proofs
- Output: count + % per mode, one example proof trace per mode

## Step Economy (RQ3)
- Histogram of total_steps for PROVED proofs
- Survival curve: P(alive after k steps) — where does it drop below 10%?
- Scatter: statement length vs steps

## History Utilisation (RQ4)
- Repetition rate per proof, aggregated
- advanced-rate by step number (line chart)
- Goal char count by step (PROVED vs FAILED separately)

## Tactic Distribution (RQ5)
- Frequency table — all tactics, sorted by count
- Precision table — top 10 by success rate
- Two-curve chart — usage vs precision by tactic rank
- Cluster into: reliable closers / progress makers / dead ends

## Tooling
All four scripts take `--logs-dir <run>` or `--all-runs`, write text to stdout or `--out`, and machine-readable JSON to `--json-out`. `build_dashboard_data.py` merges all three into `dashboard_data.json` for `results_dashboard.html`. Units convention: `*_pct` / `ci_*` are percentages 0–100; `*_rate` / `precision` are fractions 0–1.

---

# PART 10: EXPECTED RESULTS

| Metric | Prediction | Basis |
|--------|-----------|-------|
| Easy pass rate | 15–35% | SorryDB agentic baselines |
| Medium pass rate | 3–12% | ~half of Easy |
| Hard pass rate | 0–3% | May be zero |
| Median steps to proof | 3–5 (post bug fix) | toy_comm proved step 6 WITH 5 wasted empty steps |
| Dominant failure | No-progress loop ~40% | Model stuck on same goal |
| Hallucinated lemma | ~25–30% of failures | Known LLM pathology |
| Tactic repetition | > 20% | Model underutilises history |
| Most-used tactic | simp or rw | Dominate Lean training data |
| Highest-precision | omega or rfl | Decidable: always work if applicable |

**What "interesting" looks like:**
- Easy > 40%: Nemotron surprisingly capable
- Easy < 10%: bug fix insufficient or model doesn't know Lean
- Hallucinated lemma dominant: adding LeanSearchClient is the #1 fix
- Repetition < 10%: history prompt works, no-progress is the real enemy
- Medium ≈ Easy: tier labels not agent-calibrated — dataset finding

**Early signal (Easy-5, 5/5 proved; Easy-50 run 1 partial):** all proved proofs closed at step 1–4, and in run 1 all 3 closed at **step 1** with zero closing at steps 2–15 — a 15-step budget may buy nothing over a 1-step budget. Failed proofs' goals *grew* (85 → 90 chars from step 1 to 10) while proved proofs' stayed flat, consistent with churn rather than decomposition. Repetition was 22.2% pooled, matching the >20% prediction.

---

# PART 11: WHAT THE PAPER WILL BE

## Title Candidates
- "GreedyProver: Characterising the Baseline Performance and Failure Modes of Frontier LLM Agents on Real Mathlib Theorems"
- "One Tactic at a Time: A Systematic Evaluation of Zero-Shot Agentic Theorem Proving in Lean 4"
- "When LLMs Prove Lean Theorems: A Failure Mode Taxonomy and Reproducible Benchmark"

## Structure

| Section | Length | Content |
|---------|--------|---------|
| Abstract | 250 words | Problem, method, findings, benchmark release |
| 1. Introduction | 1.5 pages | Motivation, gap, contributions |
| 2. Background | 1 page | Lean 4, Mathlib, lean_interact, related work |
| 3. System | 1.5 pages | Harness, JSONL schema, proof loop |
| 4. Dataset | 0.5 pages | candidates.jsonl stats, tier distribution |
| 5. Experiments | 0.5 pages | Run configs, Nemotron-550B, checklist |
| 6. Results (RQ1–RQ5) | 3 pages | Tables, charts, statistical tests |
| 7. Failure Analysis | 1.5 pages | Taxonomy + 3 case studies |
| 8. Discussion | 1 page | What to fix first, limitations |
| 9. Conclusion | 0.5 pages | Summary, benchmark release, future work |
| Appendix | Unlimited | Full JSONL schema, proof traces, tactic tables |

## Contributions
1. Reproducible, model-agnostic evaluation harness (open-source — swap one env var)
2. 837 real Mathlib theorems, 3 difficulty tiers, structured per-step JSONL logs
3. First published failure-mode taxonomy for LLM-based Lean agents
4. Nemotron-550B greedy baseline — largest model evaluated on this task
5. Evidence for/against history utilisation by frontier LLMs
6. Tier calibration analysis: do E/M/H labels predict agent performance?
7. **Verbatim transcripts** — every prompt and raw completion logged, so results are auditable and replayable without re-running

## How to Strengthen It
- Run 2–3 models (add Llama-3.1-8B via Groq) → comparison table → week of extra runs
- This turns "single-system evaluation" into "benchmark paper" → stronger submission

---

# PART 12: RELATED WORK

| System | Approach | Best Result | Gap vs Our Work |
|--------|---------|------------|-----------------|
| Seed-Prover (2025) | RL + subgoal decomp | 99.6% miniF2F | Proprietary fine-tune; competition maths |
| BFS-Prover V2 | Multi-agent tree search + RL | 95.08% miniF2F | Massive tree search; no failure analysis |
| Kimina-Prover | Reasoning-driven, scales w/ size | 92.2% miniF2F | Fine-tuned; not zero-shot |
| LEAP (2026) | Better prompting + search | 70% Lean-IMO-Bench | Uses search; not pure greedy |
| Discover & Prove | NL discovery + ATP prover | SOTA CombiBench | Hybrid; not pure LLM agent |
| SorryDB (ICLR 2026) | Live benchmark, real Mathlib | Agentic ~20–30% easy | No structured failure analysis |
| **THIS WORK** | Greedy, 550B, zero fine-tune | TBD | First exhaustive failure taxonomy |

**Positioning:** Not competing on pass rate. Providing the definitive characterisation of the simplest possible agent: what it achieves, and exactly why it fails. The necessary baseline for every paper that proposes improvements.

---

# PART 13: TO-DO LIST

## Completed
- [x] Full harness: lean_agent.py, logger.py, batch_runner.py, run_proofs.py
- [x] Empty tactic retry fix + accurate llm_calls / lean_calls accounting
- [x] Complete verbatim transcript logging (llm_input, llm_raw_response, retry_attempts, history_passed, lean_raw_elaboration)
- [x] LeanError crash fix at both the tactic and elaboration call sites
- [x] Tactic extractor `_extract_tactic()` for reasoning-model prose
- [x] Split circuit breaker: connectivity retry/backoff vs model fail-fast
- [x] Per-call timeouts: LLM 600s, Lean elaboration 900s, Lean tactic 120s, REPL restart on kill
- [x] 367 mock tests across 7 files in harness/tests/
- [x] analyze.py — pass rate + Wilson CI, step economy, tactic frequency/precision (RQ1/3/5)
- [x] classify_failures.py — five-mode taxonomy with examples (RQ2)
- [x] history_analysis.py — repetition, advanced-rate chart, goal shrinkage (RQ4)
- [x] build_dashboard_data.py — merged JSON feed
- [x] results_dashboard.html — Chart.js dashboard, theme-aware, placeholder state
- [x] Smoke test proved (toy_comm)
- [x] **Easy-5 run: 5/5 proved**

## In Progress
- [ ] **Easy-50 run 3** — `C:\lean\logs\20260909_002719\`, wrapper PID 35580

## Next
- [ ] Run analyze.py / classify_failures.py / history_analysis.py on Easy-50 results
- [ ] Build dashboard_data.json and review the dashboard
- [ ] Launch Medium-50
- [ ] Launch Hard-50
- [ ] Draft paper sections 1–5
- [ ] Full paper draft
- [ ] Submission to AITP / workshop

## Also Outstanding
- [ ] Write report for professor
- [ ] Build presentation slides for professor
- [ ] Multi-model comparison (add Llama-3.1-8B via Groq)
- [ ] Pick 3 case studies (1 proved, 1 no-progress loop, 1 hallucinated lemma)

---

# PART 14: KEY DESIGN DECISIONS

1. **Shared LeanServer across batch** — import Mathlib paid once (~9–13 min, now cached permanently)
2. **PYTHONIOENCODING=utf-8** — prevents cp1252 Unicode crash on Windows terminal
3. **max_steps=15** — generous budget; proved proofs expected to close in <6 steps
4. **Circuit breaker** — MAX_CONSECUTIVE_LLM_FAILURES=3 → FAILED_LLM_ERROR
5. **override=False in load_dotenv** — real env vars always win over .env
6. **ASCII status strings** — "[PROVED]" not "✓ PROVED" — no encoding dependency
7. **JSONL with immediate flush** — every event durable on disk; session crashes don't lose data
8. **lean_calls=0 for rejected** — Pydantic validation failure never reaches Lean REPL
9. **_tag_call_count on exceptions** — llm_calls accurate even on failure paths
10. **Verbatim transcript fields** — `llm_raw_response` is logged *before* extraction, so a bad tactic can be blamed on the model or on our parser. `history_passed` is logged before the step appends its own entry, making it a true per-step snapshot.
11. **`_extract_tactic()` scans bottom-up** — a reasoning model states its answer last. It strips code fences and inline backticks, and returns the last line that looks like a tactic; if none qualifies it falls back to the last non-empty line, so a single-line response always survives.
12. **Keyword match is necessary but not sufficient** — a line whose head is a tactic keyword is accepted only if what follows is *not* narration. `exact Nat.add_comm _ _` and `simp only [h]` pass (dotted path / Lean connective); `simp will rewrite the goal` and `exact because the term...` are rejected. Without this, a backtick left glued to the head let a 200-character English sentence through as a tactic (run 1, proof_0004 step 3).
13. **Split circuit breaker** — connectivity and model failures counted independently. A ~40-second NIM outage destroyed 12 consecutive theorems under a single shared counter. Only a *successful* call resets a counter; a model failure does not reset the connectivity budget.
14. **LLM_CALL_TIMEOUT_SECONDS=600** — run 1 proof_0024 sat inside one un-timed-out socket call for **22.3 hours** and did zero work. 15 steps × 600s bounds a proof at 2.5h.
15. **LEAN_ELABORATION_TIMEOUT_SECONDS=900 / LEAN_TACTIC_TIMEOUT_SECONDS=120** — enforced via `LeanServer.run(timeout=)`. They must differ: the elaboration Command re-imports Mathlib inside the command (~230s measured), so a flat 120s timed out every elaboration and killed run 2 on theorem 1 of 50. `lean_interact` calls `kill()` on timeout, so `_ensure_server_alive()` restarts the REPL.
16. **Every Lean call is contained** — both the elaboration and tactic call sites are wrapped, so one bad theorem costs one theorem and `summary.json` still gets written. An unwrapped elaboration call is what turned a timeout into a whole-batch crash.
17. **MAX_TOKENS=8192** — raised 150 → 2048 → 8192. Nemotron is a reasoning model that emits ~1700+ reasoning tokens before answering; 150 truncated mid-sentence and sent the fragment to Lean, and 2048 still truncated on set-theory goals.
18. **Long runs launched detached via `Start-Process`** — a run parented to a tool/agent background task dies when that task is reaped.

---

# PART 15: ACTUAL PROOF TRACE (Only Completed Run)

Run: 20260818_090232. toy_comm (a b : Nat) : a + b = b + a. Before fixes.

```json
{"event": "start", "goal": "a b : N\n|- a + b = b + a", "timestamp": "2026-08-18T04:01:11Z"}
{"event": "step", "step": 1, "tactic": "", "outcome": "rejected",
 "lean_messages": ["validation error: tactic String should have at least 1 character"],
 "llm_calls": 1, "lean_calls": 1}
// steps 2-5: identical empty tactic bug
{"event": "step", "step": 6, "tactic": "exact Nat.add_comm _ _", "outcome": "proved",
 "lean_messages": [], "llm_calls": 1, "lean_calls": 1}
{"event": "done", "result": "PROVED", "total_steps": 6,
 "tactic_outcomes": {"rejected": 5, "proved": 1},
 "tactics_used": ["", "", "", "", "", "exact Nat.add_comm _ _"],
 "lean_calls_per_step": 1.0, "llm_calls_per_step": 1.0}
```

Note: lean_calls=6 is inflated (empty tactics never reached Lean). Real lean_calls = 1. This bug is fixed.

---

*End of reference document. Paste into any Claude chat for full project context.*
