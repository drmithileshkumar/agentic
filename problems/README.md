# Problem Set — Domain × Difficulty

Naming convention: problem id `{DOMAIN}-{TIER}-{NN}` → file
`problems/{DOMAIN}/{TIER}/{DOMAIN}_{TIER}_{NN}.lean` with a two-digit `NN`
(e.g. `SET-E-01` → `problems/SET/E/SET_E_01.lean`). The theorem inside the file
carries the underscore form of the id as its name.

## Domains

| Code | Domain              | Primary sources                                    | Pool size (raw) |
|------|---------------------|----------------------------------------------------|-----------------|
| SET  | Set Theory          | PutnamBench + Mathlib-native                       | 8+ (unlimited Mathlib) |
| TOP  | Topology            | ProofNetSharp + Mathlib-native                     | ~50–80 of 371   |
| ALG  | Algebra             | PutnamBench + miniF2F + FormalMATH                 | 253+            |
| ABA  | Abstract Algebra    | PutnamBench + ProofNetSharp                        | 28+             |
| LIN  | Linear Algebra      | PutnamBench + ProofNetSharp                        | 53+             |
| NUM  | Number Theory       | PutnamBench + miniF2F + FormalMATH                 | 113+            |
| RAN  | Real Analysis       | PutnamBench + ProofNetSharp                        | 229+            |
| CAN  | Complex Analysis    | ProofNetSharp                                      | subset of 371   |
| CMB  | Combinatorics       | PutnamBench + FormalMATH                           | 33+             |
| GEO  | Geometry            | PutnamBench ⚠️ (high malformed rate)               | 71              |
| PRB  | Probability         | PutnamBench (smallest pool, hand-curated)          | 10              |

## Difficulty Tiers

| Tier | Definition                          | Source mapping                                      |
|------|-------------------------------------|-----------------------------------------------------|
| E    | Easy — 1–4 tactics, single concept  | Mathlib-native + FormalMATH-Lite + miniF2F easy     |
| M    | Medium — multi-step, undergrad      | ProofNetSharp (all 371) + mid FormalMATH-All        |
| H    | Hard — competition / proof search   | PutnamBench (all) + hardest FormalMATH-All          |

## Status of seed set (9 verified ✅)

| File              | Domain | Tier | Statement                                   |
|-------------------|--------|------|---------------------------------------------|
| SET/E/SET_E_01    | SET    | E    | A ∩ B = B ∩ A                               |
| SET/E/SET_E_02    | SET    | E    | A ∩ (B ∪ C) = (A ∩ B) ∪ (A ∩ C)           |
| SET/E/SET_E_03    | SET    | E    | Subset transitivity (2-step)                |
| SET/E/SET_E_04    | SET    | E    | Subset chain (3-step)                       |
| TOP/E/TOP_E_01    | TOP    | E    | Continuity via preimage-of-open             |
| TOP/E/TOP_E_02    | TOP    | E    | Composition of continuous functions         |
| ALG/E/ALG_E_01    | ALG    | E    | (a * b)⁻¹ = b⁻¹ * a⁻¹                      |
| ALG/E/ALG_E_02    | ALG    | E    | Homomorphisms preserve inverses             |
| ALG/E/ALG_E_03    | ALG    | E    | (a + b)(a - b) = a² - b²                   |

## Adding new problems

1. Run `python problems/extract_candidates.py --source <dataset dir>` → appends to `candidates.jsonl`
2. Inspect it: `python problems/manifest.py list --pending` / `show <id>`
3. Write it into the tree: `python problems/manifest.py promote <id>`
4. Open the generated file in VS Code, confirm it elaborates and is provable
5. Flip the manifest flag: `python problems/manifest.py verify <id>`
6. Only verified entries feed the harness — `manifest.py check` enforces this

See `SETUP_COMPLETE.md` for the full tool reference.
