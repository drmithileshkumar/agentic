# Resource Library

*Agentic Hallucinations — Independent Study*
*Living document. Every deliverable in this project should be checked against and cited from this list. Add new sources here as they're found — do not let citations live only inside individual docs.*

**Maintenance protocol:** before writing or revising any doc in this project, (1) check this file for existing relevant sources, (2) search for anything new specific to that doc's topic, (3) add new finds here with a one-line relevance note before using them.

---

## 1. Lean 4 — Core Learning

| Resource | Type | Link | Relevance |
|---|---|---|---|
| **Theorem Proving in Lean 4** — Lean community / Lean FRO | Book (official) | [leanprover.github.io/theorem_proving_in_lean4](https://leanprover.github.io/theorem_proving_in_lean4/) | Canonical reference for Lean 4's type theory, tactic language, and proof term syntax. Primary source for the tutorial and cheat sheet. |
| **Mathematics in Lean** (v4.19+) — Jeremy Avigad, Patrick Massot | Book / exercise set | [leanprover-community.github.io/mathematics_in_lean](https://leanprover-community.github.io/mathematics_in_lean/) ([PDF](https://leanprover-community.github.io/mathematics_in_lean/mathematics_in_lean.pdf)) | Mathlib-style tactic proofs across real math topics (analysis, algebra, topology, sets) — directly feeds the topic-segmented problem set (Goal 9). |
| **Functional Programming in Lean** — Lean FRO | Book (official) | [leanprover.github.io/functional_programming_in_lean](https://leanprover.github.io/functional_programming_in_lean/) | Lean-as-a-language fundamentals (term syntax, types, metaprogramming basics) underlying tactic-mode proofs. |
| **Natural Number Game** | Interactive tutorial | [adam.math.hhu.de/#/g/leanprover-community/nng4](https://adam.math.hhu.de/#/g/leanprover-community/nng4) | Fastest on-ramp to basic tactics (`rfl`, `rw`, `induction`) via a game interface. Good first-session exercise before the cheat sheet. |
| **The Hitchhiker's Guide to Logical Verification** — VU Amsterdam | Course textbook | via [leanprover-community.github.io/learn.html](https://leanprover-community.github.io/learn.html) | Graduate-level treatment; useful once tactics from Mathematics in Lean feel routine. |
| **Metaprogramming in Lean** | Book/tutorial | via community learn page | Reference for later if we need custom tactics for the harness (e.g. auto-formatting agent output into valid syntax). |
| **A Comprehensive Survey of the Lean 4 Theorem Prover: Architecture, Applications, and Advances** | Paper (arXiv 2501.18639, 2026) | [arxiv.org/abs/2501.18639](https://arxiv.org/abs/2501.18639) | Academic overview of Lean 4's architecture — useful for the "how it works" background section of the tutorial and pipeline doc. |
| **Lean Language Reference** | Official reference | [lean-lang.org/doc/reference/latest](https://lean-lang.org/doc/reference/latest/) | Ground-truth spec for exact tactic/syntax semantics when the tutorial needs precision beyond examples. |
| **Mathlib4 API Docs** | Reference | [leanprover-community.github.io/mathlib4_docs](https://leanprover-community.github.io/mathlib4_docs) | Lemma/tactic lookup. Includes the canonical [tactic list](https://leanprover-community.github.io/mathlib4_docs/tactics.html). |
| **Mathlib naming conventions** | Reference | [leanprover-community.github.io/contribute/naming.html](https://leanprover-community.github.io/contribute/naming.html) | The systematic scheme behind every lemma name (`snake_case` for proofs, symbol→word dictionary, hypothesis-ordering via "of", `.ext`/`.inj` suffix rules). Explains *why* "wrong lemma name" is a learnable/predictable hallucination category rather than pure noise — feeds tutorial §15 and the eventual error taxonomy. |

## 1a. Lean 4 Dev — Tactic Course (primary source for cheat sheet + tutorial)

| Resource | Link | Relevance |
|---|---|---|
| **Lean 4 Dev — Tactics course** (35-tactic curriculum, Modules 1–6: Core, Automation, Advanced, Mathlib) | [lean4.dev/tactics](https://lean4.dev/tactics) | Best-structured teaching resource found: one page per tactic with definition, "when to use," common mistakes, and 3+ worked examples each. Primary source for `02_cheat_sheet.md` and `03_tutorial.md`. Pages fetched directly and cross-checked: `core/rw`, `core/simp`, `core/exact-apply`, `core/intro`, `core/have-let`, `core/cases-induction`, `automation/omega`, `mathlib/ring`, `mathlib/rcases`, `mathlib/ext`, `advanced/calc`, `advanced/by-contra`. |
| **Tactic Cheat Sheet (lean4.dev)** | [lean4.dev/tactics/cheat-sheet](https://lean4.dev/tactics/cheat-sheet) | Categorized index of all 35 tactics (Core/Automation/Advanced/Mathlib) — used as the skeleton for our own cheat sheet's category structure. |
| **Lean 4 tactic cheatsheet (PDF)** — leanprover-community | [leanprover-community.github.io/papers/lean-tactics.pdf](https://leanprover-community.github.io/papers/lean-tactics.pdf) | Community-maintained PDF cheat sheet; cross-reference against lean4.dev's version for anything that looks off. |
| **madvorak/lean4-tactics** (GitHub) | [github.com/madvorak/lean4-tactics](https://github.com/madvorak/lean4-tactics) | Longer-form beginner tactic overview; secondary cross-check source. |

## 2. Search & Discovery Tools

| Resource | Link | Relevance |
|---|---|---|
| **Loogle!** | [loogle.lean-lang.org](https://loogle.lean-lang.org/) | Type/name-based Mathlib search — reference in the cheat sheet as the "how do I find the lemma I need" tool. |
| **LeanSearch** | via community learn page | Natural-language → Mathlib lemma search. Same use case as Loogle, complementary. |
| **Lean Zulip** | [leanprover.zulipchat.com](https://leanprover.zulipchat.com/) | Community Q&A; primary source for troubleshooting edge cases not covered in docs. |

## 3. Formal Math Benchmarks / Problem Sets (feeds Goal 9)

| Benchmark | Scope | Source | Relevance |
|---|---|---|---|
| **miniF2F** | High-school competition math (AMC, AIME, IMO), 244 val / 244 test | GitHub `openai/miniF2F` (Lean port maintained by community) | Bottom of our difficulty ladder — algebra/number theory, short proofs. |
| **ProofNet** | Undergraduate: real/complex analysis, linear algebra, abstract algebra, topology (no dedicated "set theory" category), 371 problems | [arXiv:2302.12433](https://arxiv.org/abs/2302.12433), [HF dataset](https://huggingface.co/datasets/hoskinson-center/proofnet) | Middle of our difficulty ladder; covers our topology and algebra tiers directly. **Correction (checked the repo directly):** [zhangir-azerbayev/ProofNet](https://github.com/zhangir-azerbayev/ProofNet) is the **original Lean 3** version — its own README explicitly warns it's unmaintained and to use a Lean 4 port instead, e.g. the one bundled in [DeepSeek-Prover-V1.5](https://github.com/deepseek-ai/DeepSeek-Prover-V1.5), or **PAug/ProofNetSharp** (the HF dataset LeanInteract's own examples use — see `06_harness_setup.md`'s dependency). Use one of the Lean 4 ports, never the original repo, for any statement that goes into our problem set. |
| **PutnamBench** | Putnam competition 1962–2025, **1,724 total / 672 in Lean 4** | [GitHub](https://github.com/trishullab/PutnamBench) ([official stats](https://github.com/trishullab/PutnamBench#statistics)), [arXiv:2407.11214](https://arxiv.org/pdf/2407.11214) | Top of our difficulty ladder. **Official category breakdown (some overlap between categories):** Algebra 253, Analysis 229, Number Theory 113, Geometry 71, Linear Algebra 53, Combinatorics 33, Abstract Algebra 28, Probability 10, **Set Theory 8**. Correction to earlier note: PutnamBench *does* have a (small) Set Theory category — checked the repo's own README directly. Lean 4 files live in the repo's `lean4/` folder, clonable directly. |
| **miniF2F-Lean Revisited** | Critique + fixes of miniF2F's Lean formalizations | [arxiv.org/pdf/2511.03108](https://arxiv.org/pdf/2511.03108) (2026) | Important: flags known errors/ambiguities in miniF2F's Lean statements — check before pulling problems from it. |
| **FormalMATH** | Large-scale Lean 4 benchmark, **5,560 problems** (high-school Olympiad through undergraduate) | [GitHub](https://github.com/Sphere-AI-Lab/FormalMATH-Bench), [HF (SphereLab org)](https://huggingface.co/SphereLab), [arXiv:2505.02735](https://arxiv.org/abs/2505.02735) | Domains: algebra, applied math, calculus, number theory, discrete math. Two splits available: `FormalMATH-All` (5,560) and a smaller `FormalMATH-Lite`. Auto-downloadable (`--auto_dl` flag in their own eval script). **Notable synergy: their own verification pipeline uses `leanprover-community/repl`** — the exact same REPL our harness (`06_harness_setup.md`) is built on. By far our largest single candidate pool. |

## 4. LLM ↔ Lean Agent Systems (feeds Goal 6: pipeline design)

| System | Paper | Relevance |
|---|---|---|
| **LeanDojo / ReProver** | [arXiv 2306.15626](https://arxiv.org/abs/2306.15626) | Reference architecture for programmatic Lean interaction: extracts proof states, exposes a Gym-like tactic-execution interface. Closest existing analog to our harness (§6) — study its state/action interface design directly. |
| **PACT (Proof Artifact Co-training)** | [arXiv 2102.06203](https://arxiv.org/abs/2102.06203) | Early precedent for training/prompting on kernel-level proof state; useful for how to represent goal states to an agent. |
| **Lean Copilot** | [arXiv 2404.12534](https://arxiv.org/html/2404.12534v2) | Runs LLM inference *natively inside* Lean (vs. external harness) — a design alternative worth contrasting against our external-loop approach in the pipeline doc. |
| **DeepSeek-Prover (V1 / V1.5 / V2)** | [arXiv 2405.14333](https://arxiv.org/pdf/2405.14333) | Large-scale synthetic proof data + RL from proof-assistant feedback + MCTS — relevant precedent for treating verifier feedback as a training/analysis signal. |
| **LeanAgent** | [arXiv 2410.06209](https://arxiv.org/html/2410.06209v8) | Lifelong-learning agent over Lean repos — relevant for how to structure a persistent agent-harness architecture rather than one-shot calls. |
| **Prover Agent** | [arXiv 2506.19923](https://arxiv.org/html/2506.19923v4) | Recent agent-based framework explicitly structured around iterative tactic proposal + verifier feedback — closely mirrors our agent loop design. |
| **APOLLO (Automated LLM + Lean Collaboration)** | [arXiv 2505.05758](https://arxiv.org/html/2505.05758v5) | Another recent agent/Lean collaboration framework — compare error-handling/retry strategy against ours. |
| **AlphaProof / AlphaGeometry 2** (DeepMind, IMO 2024) | DeepMind blog + coverage | RL-based, not a direct architectural template (closed system) but useful as the upper bound of what's been achieved and a citation point for framing. |

### Concrete machine-interaction interfaces to Lean 4 (the actual plumbing options for our harness)

| Interface | Link | Relevance |
|---|---|---|
| **leanprover-community/repl** | [github.com/leanprover-community/repl](https://github.com/leanprover-community/repl) | JSON-over-stdin/stdout REPL, purpose-built for exactly our loop: `{"tactic": "...", "proofState": n}` → `{"proofState": n+1, "goals": [...]}`. Supports command mode (whole declarations) and experimental tactic mode (one tactic at a time, with `env`/`proofState` integers for backtracking), plus pickling proof states to `.olean` for persistence across sessions. **Primary candidate interface** — README fetched in full, protocol documented directly in `04_pipeline.md`. |
| **lean-lsp-mcp** | [github.com/oOo0oOo/lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp) | Wraps Lean's actual language server (the same one VS Code talks to, via `lake serve`) in an MCP server for agents; JSON-RPC 2.0; `lean_goal` tool returns structured goal state at a file position. Interesting because it reuses the *interactive* interface rather than a separate batch REPL — tradeoff discussed in `04_pipeline.md`. |
| **Pantograph** | [arXiv:2410.16429](https://arxiv.org/abs/2410.16429) (Aniva, Sun, Miranda, Barrett, Koyejo) | Purpose-built machine-to-machine API/REPL for Lean 4, designed explicitly for ML proof search (supports MCTS); used in Draft-Sketch-Prove-style pipelines combining LLMs with Lean tactics. More feature-rich than the plain REPL but a heavier dependency — noted as a fallback option if the plain REPL proves too limited (e.g. for search-tree/backtracking-heavy agent designs). |
| **LeanInteract** (`pip install lean-interact`) | [docs](https://augustepoiroux.github.io/LeanInteract/), [PyPI](https://pypi.org/project/lean-interact/), [GitHub](https://github.com/augustepoiroux/leaninteract) | **Confirmed as the actual harness dependency** (not just a candidate) — full README fetched. Wraps the REPL from `04_pipeline.md` §3 with a typed Python API (`LeanServer`, `Command`, `ProofStep`) that matches our data model almost field-for-field: `ProofStep(tactic=..., proof_state=n)` → `ProofStepResponse(proof_state=n+1, goals=[...], proof_status=...)`. Supports pointing at our *existing* `MyMathlibProject` via `LocalProject(directory=...)` rather than spinning up a temp project. Also does environment/proof-state pickling, and an `AutoLeanServer` variant that auto-recovers from crashes/timeouts — directly relevant to `04_pipeline.md` §6's plumbing-failure handling. Setup steps: `06_harness_setup.md`. |

## 5. LLM Hallucination Literature (core analytical framework — Goal 8)

| Paper | Link | Relevance |
|---|---|---|
| **A Survey on Hallucination in Large Language Models: Principles, Taxonomy, Challenges, and Open Questions** — Huang et al. | [arXiv 2311.05232](https://arxiv.org/abs/2311.05232) (also [ACM TOIS](https://dl.acm.org/doi/10.1145/3703155)) | Primary taxonomy source (factuality vs. faithfulness hallucination) — starting point for our error-category schema before specializing to tactic-level errors. |
| **Large Language Models Hallucination: A Comprehensive Survey** | [arXiv 2510.06265](https://arxiv.org/html/2510.06265v2) (2026) | More recent; ties taxonomy to the LLM dev cycle and to detection method categories (retrieval, uncertainty, embedding, learning, self-consistency) — useful for designing our own detection/logging schema. |
| **LLM-based Agents Suffer from Hallucinations: A Survey of Taxonomy, Methods, and Directions** | [arXiv 2509.18970](https://arxiv.org/html/2509.18970v1) | Most directly relevant: taxonomizes hallucination by *agent component* (internal state vs. external behavior/action) — maps naturally onto "wrong tactic chosen" (action-level) vs. "misread goal state" (internal-state-level) distinctions we'll want in our own categories. |
| **A Survey on Large Language Model Hallucination via a Creativity Perspective** | [arXiv 2402.06647](https://arxiv.org/pdf/2402.06647) | Alternative framing (hallucination as a spectrum with creativity) — background/contrast material for the intro/motivation section of any paper we publish. |

## 6. Formal Verification as Ground Truth (directly motivates this project's method)

| Source | Link | Relevance |
|---|---|---|
| **Mathematics with large language models as provers and verifiers** | [arXiv 2510.12829](https://arxiv.org/pdf/2510.12829) | Closest existing framing of LLM-as-prover with formal-verifier-as-oracle; compare our statistical framing against theirs. |
| **The 4/δ Bound: Designing Predictable LLM-Verifier Systems for Formal Method Guarantee** | [arXiv 2512.02080](https://arxiv.org/pdf/2512.02080) (2026) | Theoretical framing of predictability/guarantees in LLM+verifier systems — potentially useful for the statistical-rigor angle when we get to analysis. |
| **Generating Natural Language Proofs with Verifier-Guided Search** | [arXiv 2205.12443](https://arxiv.org/pdf/2205.12443) | Earlier (non-Lean) precedent for verifier-guided search loops — historical grounding. |

---

## 7. Project publication tooling (repo structure, docs site)

| Resource | Link | Relevance |
|---|---|---|
| **leanblueprint** (Patrick Massot) | [github.com/PatrickMassot/leanblueprint](https://github.com/PatrickMassot/leanblueprint) | plasTeX plugin used across dozens of real Lean formalization projects (Sphere Eversion, Liquid Tensor Experiment, FLT, PFR...) to write a LaTeX "blueprint" that auto-builds into a website with a dependency graph, tracking which statements are stated/proved/formalized. **Confirmed source-folder convention**: `blueprint/src/` holds `web.tex`/`print.tex`/`content.tex`; `leanblueprint web`/`pdf`/`serve`/`checkdecls` build it. Install: `pip install leanblueprint` (needs graphviz + a TeX install for the PDF path). Assumes a GitHub-hosted repo with GitHub Pages via Actions. Professor-requested doc/repo style — full setup task added to the project todo list. |

## 8. Lean's own intermediate-step capture (candidate for Goal 8's logging schema)

| Resource | Link | Relevance |
|---|---|---|
| **`Lean.Elab.InfoTree`** (core Lean, `Lean.Elab.InfoTree.Types`) | [mathlib4_docs](https://leanprover-community.github.io/mathlib4_docs/Lean/Elab/InfoTree/Types.html) / [source](https://github.com/leanprover/lean4/blob/master/src/Lean/Elab/InfoTree/Types.lean) | Lean's **own built-in** structure recording elaboration steps — this is what the language server itself uses to power the Infoview. The `TacticInfo` node specifically stores `goalsBefore`/`goalsAfter` (as `MVarId` lists) plus `mctxBefore`/`mctxAfter` (metavariable context) and `stx` (the tactic's actual syntax) for every tactic executed. Structurally almost identical to what `04_pipeline.md` §4 already logs from REPL text — the open design question for Goal 8 is REPL-text logging (simpler, already working) vs. tapping `InfoTree` directly (richer/structured, more setup, and what LeanInteract's "info tree" data-extraction feature is built on). |

- Formal citation for Lean 4's kernel/elaborator internals (for pipeline doc §"how Lean actually checks a tactic").
- Papers specifically measuring *iteration count to convergence* or *failure-to-terminate* rates in agent proof loops (closest to our core statistical question) — not yet found a direct match; keep searching before writing the final analysis methodology.
- Survey coverage of infinite-loop / non-termination detection in agent tool-use generally (beyond Lean) — may exist in the agent-hallucination taxonomy papers above; needs a closer read, not just abstract-level search.

---

*Last updated: 2026-07-18. Add entries above; do not delete without noting why (e.g. "superseded by X").*
