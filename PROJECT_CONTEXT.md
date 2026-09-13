# Project Memory — Agentic Theorem Proving with Lean 4
# Independent Study — anshhika (jainanshi124@gmail.com)
# Last updated: September 6, 2026

---

## What This Project Is

Independent study on **agentic theorem proving with Lean 4**. An LLM agent proposes Lean 4 tactics one at a time in a loop. Lean checks each tactic and returns the new proof state. The goal is to characterise how well a frontier LLM (no fine-tuning, no tree search) can prove real Mathlib theorems, and to build a reproducible benchmark with exhaustive failure analysis.

**Target output:** A publishable paper + benchmark. Realistic venues: AITP, ICLR/NeurIPS workshop (Math-AI), COLM.

---

## Research Questions

**RQ1 — Capability ceiling:** What pass rate does Nemotron-3-Ultra-550B achieve on Mathlib Easy/Medium/Hard?
**RQ2 — Failure taxonomy:** When it fails, WHY? (No-progress loop / error loop / hallucinated lemma / syntax error / budget exhaustion)
**RQ3 — Step economy:** How many steps do proved proofs take? Is there a point of no return?
**RQ4 — History utilisation:** Does the model actually use its tactic history, or repeat mistakes?
**RQ5 — Tactic distribution:** Which tactics does it use? Which ones actually work (precision)?

---

## Stack

- **Lean 4** + Mathlib v4.33.0-rc1
- **lean_interact** library (LeanServer, ProofStep, Command, LocalProject, LeanREPLConfig)
- **LLM:** NVIDIA Nemotron-3-Ultra-550B via NVIDIA NIM API (OpenAI-compatible)
- **API base URL:** https://integrate.api.nvidia.com/v1
- **Model name:** nvidia/nemotron-3-ultra-550b-a55b
- **Credentials:** Stored in C:\lean\.env (loaded automatically by run_proofs.py via python-dotenv)
- **Problem set:** C:\lean\MyMathlibProject\problems\candidates.jsonl — 837 problems, tiers E/M/H (310/378/149)
- **Lean project:** C:\lean\MyMathlibProject (Mathlib v4.33.0-rc1)
- **Python venv:** C:\lean\harness-venv

---

## File Structure

```
C:\lean\
├── .env                          ← API credentials (never commit)
├── run_proofs.py                 ← CLI entry point
├── analyze.py                    ← (to be written Sep 3) results analysis
├── classify_failures.py          ← (to be written Sep 3) failure mode classifier
├── results_dashboard.html        ← (to be built Sep 5) professor demo
├── AgenticTheoremProving_ResearchPlan.docx  ← full research doc
├── PROJECT_CONTEXT.md            ← this file — paste into any new chat
├── harness/
│   ├── __init__.py
│   ├── lean_agent.py             ← LeanAgentHarness: proof loop, LLM calls
│   ├── logger.py                 ← ProofLogger: JSONL event logging
│   └── batch_runner.py           ← BatchRunner: runs many proofs, shared LeanServer
├── logs/
│   └── <timestamp>/
│       ├── proof_0001.jsonl      ← per-proof event log
│       └── summary.json          ← aggregate stats
└── MyMathlibProject/
    ├── problems/candidates.jsonl
    └── .lake/                    ← Lean build cache (WARM — 8,279 .olean files)
```

---

## How to Run

```powershell
# Credentials auto-load from C:\lean\.env — no manual export needed
cd C:\lean
.\harness-venv\Scripts\Activate.ps1

# Smoke test (1 proof)
python -u run_proofs.py --theorem "import Mathlib`n`ntheorem toy_comm (a b : Nat) : a + b = b + a := by sorry" --max-steps 6

# 5 Easy proofs
python -u run_proofs.py --difficulty Easy --limit 5 --max-steps 15

# 50 Easy proofs (overnight)
python -u run_proofs.py --difficulty Easy --limit 50 --max-steps 15 --no-echo

# 50 Medium / Hard
python -u run_proofs.py --difficulty Medium --limit 50 --max-steps 15 --no-echo
python -u run_proofs.py --difficulty Hard --limit 50 --max-steps 15 --no-echo
```

---

## Outcome Taxonomy

| Outcome | Meaning |
|---------|---------|
| proved | Tactic closed all goals |
| advanced | Goal state changed (progress) |
| no_progress | Lean accepted tactic but goal unchanged |
| error | Lean returned error messages |
| rejected | lean_interact raised exception (incl. empty tactic) |

**Proof-level results:** PROVED / FAILED_BUDGET_EXHAUSTED / FAILED_ELABORATION / FAILED_LLM_ERROR

---

## JSONL Schema (per step event)

```json
{
  "proof_id": "uuid4",
  "theorem": "import Mathlib\n\ntheorem ...",
  "event": "step",
  "step": 1,
  "tactic": "exact Nat.add_comm _ _",
  "outcome": "proved",
  "goal_before": "a b : N\n|- a + b = b + a",
  "goal_after": null,
  "lean_messages": [],
  "llm_calls": 1,
  "lean_calls": 1,
  "timestamp": "2026-09-02T..."
}
```

---

## Failure Mode Classifier (pseudocode)

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
    parse_errors = [s for s in steps if any(k in " ".join(s["lean_messages"]) for k in ["expected", "parse error"])]
    if len(parse_errors) >= 3:
        return "SYNTAX_ERROR_LOOP"
    return "BUDGET_EXHAUSTION_MIXED"
```

---

## Current Status (Sep 6, 2026)

### COMPLETE
- Full harness: lean_agent.py, logger.py, batch_runner.py, run_proofs.py
- Empty tactic retry fix (up to 3 retries with nudge message)
- Real llm_calls counting (retries counted, exception-tagged)
- Real lean_calls counting (rejected path = 0 lean calls)
- ASCII patch (no Unicode crashes on cp1252 terminals)
- .env auto-loading via python-dotenv
- NVIDIA NIM API key set in C:\lean\.env
- Lean cache warm (8,279 .olean files, toolchain 4.33.1 installed)
- Research plan doc: C:\lean\AgenticTheoremProving_ResearchPlan.docx
- 66/66 mock tests passing

### ONE COMPLETED PROOF (before fixes)
- toy_comm (a b : Nat) : a + b = b + a → proved via: exact Nat.add_comm _ _
- 5 of 6 steps were empty tactic bug (now fixed)

### IN PROGRESS
- Smoke test with Nemotron running — waiting for result
- Need: first non-empty tactic from Nemotron to confirm all fixes work end-to-end

---

## Sep 2–6 To-Do List

- [x] Build harness (lean_agent, logger, batch_runner, run_proofs)
- [x] Fix empty tactic bug
- [x] Fix llm_calls / lean_calls counting
- [x] ASCII patch
- [x] .env + python-dotenv
- [x] NVIDIA key set
- [x] Research plan doc
- [ ] **Smoke test result — confirm Nemotron proposes non-empty tactic**
- [ ] Run 5 Easy proofs
- [ ] Run 50 Easy proofs overnight
- [ ] Write analyze.py (pass rate CI, tactic table, top tactics, avg steps)
- [ ] Write classify_failures.py (failure mode breakdown)
- [ ] Run 50 Medium proofs overnight
- [ ] Write history_analysis.py (repetition rate, advanced-rate trend, goal shrinkage)
- [ ] Run 50 Hard proofs overnight
- [ ] Build results_dashboard.html (Chart.js, KPI cards, per-proof table, case studies)
- [ ] Pick 3 case studies (1 proved, 1 no-progress loop, 1 hallucinated lemma)
- [ ] Professor demo (Sep 6)

## After Sep 6 (PENDING — do not forget)
- [ ] Write report for professor
- [ ] Build presentation slides for professor
- [ ] Multi-model comparison (add Llama-3.1-8B via Groq)
- [ ] Draft paper sections 1–5
- [ ] Full paper draft + submission

---

## Key Design Decisions

1. Shared LeanServer across batch — import Mathlib paid once (~14 min, now cached)
2. PYTHONIOENCODING=utf-8 — prevents cp1252 crash on Windows terminal
3. max_steps=15 — generous budget (proved proofs expected to close in <6 steps)
4. Circuit breaker — MAX_CONSECUTIVE_LLM_FAILURES=3 → FAILED_LLM_ERROR
5. override=False in load_dotenv — real env vars always win over .env
6. ASCII status strings — "[PROVED]" not "checkmark PROVED"

---

## Expected Results

| Metric | Prediction |
|--------|-----------|
| Easy pass rate | 15–35% |
| Medium pass rate | 3–12% |
| Hard pass rate | 0–3% |
| Dominant failure mode | No-progress loop (~40%) |
| Hallucinated lemma | ~25–30% of failures |
| Tactic repetition rate | >20% |
| Most-used tactic | simp or rw |
| Highest-precision tactic | omega or rfl |

---

## How to Use This File in a New Chat

Paste the contents of this file at the start of any new conversation, or drag it into the chat. It gives Claude full context to pick up exactly where we left off.
