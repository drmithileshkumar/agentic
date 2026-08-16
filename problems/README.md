# Problem Set — Domain × Difficulty

The benchmark manifest lives in `candidates.jsonl`: one JSON object per line,
one line per problem. Problems that have been *promoted and proved* also exist
as Lean files in this tree and are compiled by `lake build`.

Naming convention: problem id `{DOMAIN}-{TIER}-{NN}` → file
`problems/{DOMAIN}/{TIER}/{DOMAIN}_{TIER}_{NN}.lean` with a two-digit `NN`
(e.g. `SET-E-01` → `problems/SET/E/SET_E_01.lean`). The theorem inside the file
carries the underscore form of the id as its name, and the file is the Lean
module `SET.E.SET_E_01`.

## Current coverage

Regenerate this table with `python problems/manifest.py stats`.

| Code | Domain           | Easy | Medium | Hard | Total | Verified |
|------|------------------|-----:|-------:|-----:|------:|---------:|
| SET  | Set Theory       |    9 |     13 |    9 |    31 |        4 |
| TOP  | Topology         |   17 |     32 |   22 |    71 |        2 |
| ALG  | Algebra          |   86 |    103 |   13 |   202 |        3 |
| ABA  | Abstract Algebra |   59 |     50 |    7 |   116 |        0 |
| LIN  | Linear Algebra   |    6 |     19 |    3 |    28 |        0 |
| NUM  | Number Theory    |  111 |     87 |   38 |   236 |        0 |
| RAN  | Real Analysis    |   14 |     56 |   45 |   115 |        0 |
| CAN  | Complex Analysis |    7 |     12 |    9 |    28 |        0 |
| CMB  | Combinatorics    |    1 |      4 |    1 |     6 |        0 |
| GEO  | Geometry         |    0 |      1 |    2 |     3 |        0 |
| PRB  | Probability      |    0 |      1 |    0 |     1 |        0 |
|      | **All**          |  310 |    378 |  149 |   837 |        9 |

Coverage is uneven because the sources are. ProofNetSharp is undergraduate
textbook exercises (Rudin, Munkres, Dummit–Foote, Artin, Axler, Herstein,
Ireland–Rosen, Pugh, Shakarchi) and miniF2F is competition problems (AMC, AIME,
IMO, `mathd_*`). Between them they contain almost no geometry and essentially no
probability, so those buckets are nearly empty. Filling them needs a different
source, not a different classifier.

## Sources in the manifest

| Dataset | Entries | Pulled with |
|---------|--------:|-------------|
| miniF2F (`cat-searcher/minif2f-lean4`) | 457 | `--sources minif2f` |
| ProofNetSharp (`PAug/ProofNetSharp`)   | 371 | `--sources proofnet` |
| seed (hand-written, verified)          |   9 | `--seed` |

PutnamBench and FormalMATH are not wired up; add them to `DATASET_SOURCES` in
`extract_candidates.py` once their column names have been checked.

## How a problem gets its domain

Weighted keyword scoring over the Lean statement and the natural-language
statement, highest total wins. Three rules do the real work:

1. **Identifiers match on token boundaries.** Without this, `Basis` fires inside
   `IsTopologicalBasis` and Munkres' topology exercises land in Linear Algebra.
2. **The Lean statement outweighs the prose** (`STATEMENT_WEIGHT` /
   `INFORMAL_WEIGHT`). It names Mathlib types, so it says what the problem *is*.
3. **The source is evidence.** Munkres is a topology book; Axler is a linear
   algebra book. `SOURCE_PRIORS` adds a bonus and acts as the fallback when
   keywords say nothing at all.

Anything still unscored falls back to the ambient number type (`ℕ`/`ℤ` → NUM,
`ℚ`/`ℝ` → ALG, `ℂ` → CAN) and only then to `UNK`. As of this writing nothing
lands in `UNK`; `manifest.py check` fails if anything does.

Note what is *not* used: the filename. Mapping every `imo_*` to Algebra put 39
olympiad problems there, number theory and geometry included.

## How a problem gets its tier

| Tier | Meaning | Rule |
|------|---------|------|
| E | Easy — single concept | complexity ≤ 6, or the source proof closes in ≤ 3 tactics |
| M | Medium — undergraduate, multi-step | everything in between; AMC problems never fall below this |
| H | Hard — competition or deep | complexity ≥ 16, or the problem comes from IMO / Putnam / AIME / USAMO |

Complexity is a structural score over the Lean statement: hypotheses count
double, quantifiers count double, connectives count single, plus one point per
60 characters. See `statement_complexity` in `extract_candidates.py`.

This is a proxy for difficulty, not a judgement about the mathematics. Its one
virtue is that it is computed from the problem rather than from the name of the
file it arrived in — the previous rule mapped all 371 ProofNet entries to
Medium, which meant 44% of the benchmark carried no difficulty signal at all.

## Adding new problems

```bash
# 1. Harvest. Every mode merges into candidates.jsonl; nothing is dropped
#    unless you pass --replace.
python problems/extract_candidates.py --sources proofnet,minif2f

# 2. Find something to work on.
python problems/manifest.py list --pending --domain TOP --tier E
python problems/manifest.py show <id>

# 3. Write it into the tree.
python problems/manifest.py promote <id>

# 4. Prove it, then add one import line to problems/Problems.lean so the
#    build knows about it.
#      import TOP.E.TOP_E_03

# 5. Confirm Lean agrees. `Problems` is a default target, so a plain
#    `lake build` covers it.
lake build

# 6. Flip the flag, and re-check.
python problems/manifest.py verify <id>
python problems/manifest.py check

# 7. Regenerate the blueprint so the site matches the manifest.
python problems/gen_blueprint.py
```

`<id>` accepts a full id, a unique prefix, or a theorem name.

`manifest.py check` cross-checks `Problems.lean` against the manifest in both
directions: a verified entry that the index does not import is a problem nothing
compiles, and an import with no verified entry behind it is a stale line. If you
forget step 4, `check` says so.

## Tests

```bash
python problems/test_classify.py    # classification regressions
python problems/test_blueprint.py   # LaTeX sanitizer
python problems/manifest.py check   # manifest consistency
```

All three run in CI (`.github/workflows/lean_action_ci.yml`), along with a check
that `blueprint/src/content.tex` still matches what `gen_blueprint.py` produces.

See `SETUP_COMPLETE.md` for the full tool reference.
