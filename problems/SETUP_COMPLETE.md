# Setup Complete

Status report for the `problems/` benchmark pipeline. Generated 2026-08-12.

Everything below reflects the **verified end state on disk**, not intentions —
each command in this file was run before it was written down.

---

## 1. Cleanup

| # | Action | Result |
|---|--------|--------|
| 1 | Added `.gitkeep` to every empty tier directory | 30 files — git does not track empty directories, so the 11×3 grid would not have survived a clone without them |
| 2 | Extended `.gitignore` | Added `__pycache__/` and `*.pyc` (the new Python tooling lives inside the repo); `/.lake` was already ignored |
| 3 | Removed pipeline smoke-test artifacts | `problems/NUM/M/NUM_M_01.lean`, `problems/NUM/M/NUM_M_02.lean`, `problems/__pycache__/` — all written while testing `promote`, all deleted |
| 4 | Fixed a contradiction in `problems/README.md` | The header claimed `{DOMAIN}__{TIER}__{NN}.lean` (double underscore) while step 3 claimed `{NN:03d}` (three digits). Neither matched the 9 files on disk. Both were corrected to the actual convention — see §6 |
| 5 | Rewrote the "Adding new problems" section of `problems/README.md` | It referenced tools that did not exist yet; it now lists the real commands |
| 6 | Confirmed no build output is tracked | `.lake/` is ignored; `git status` shows only intended files |

**Not touched, flagged for your call:** `MyMathlibProject/Test.lean` is leftover
scaffolding (`import Mathlib.Topology.Basic` + a bare `#check`). Nothing imports
it — `MyMathlibProject.lean` imports only `Basic.lean`. It is harmless but dead.
Left in place rather than deleted, since removing files outside `problems/` was
outside the scope I could confirm.

---

## 2. Folder structure

11 domains × 3 tiers = 33 directories, all present.

```
problems/
├── README.md                  domain/tier reference tables
├── SETUP_COMPLETE.md          this file
├── candidates.jsonl           the manifest (9 verified entries)
├── extract_candidates.py      dataset → candidates.jsonl
├── manifest.py                browse / verify / promote
│
├── ABA/{E,M,H}/               .gitkeep
├── ALG/
│   ├── E/  ALG_E_01.lean  ALG_E_02.lean  ALG_E_03.lean
│   ├── M/  .gitkeep
│   └── H/  .gitkeep
├── CAN/{E,M,H}/               .gitkeep
├── CMB/{E,M,H}/               .gitkeep
├── GEO/{E,M,H}/               .gitkeep
├── LIN/{E,M,H}/               .gitkeep
├── NUM/{E,M,H}/               .gitkeep
├── PRB/{E,M,H}/               .gitkeep
├── RAN/{E,M,H}/               .gitkeep
├── SET/
│   ├── E/  SET_E_01.lean  SET_E_02.lean  SET_E_03.lean  SET_E_04.lean
│   ├── M/  .gitkeep
│   └── H/  .gitkeep
└── TOP/
    ├── E/  TOP_E_01.lean  TOP_E_02.lean
    ├── M/  .gitkeep
    └── H/  .gitkeep
```

Coverage as reported by `python problems/manifest.py stats`:

```
+--------+-----+-----+-----+-------+
| domain |   E |   M |   H | total |
+--------+-----+-----+-----+-------+
| SET    | 4/4 |   - |   - |   4/4 |
| TOP    | 2/2 |   - |   - |   2/2 |
| ALG    | 3/3 |   - |   - |   3/3 |
| ALL    | 9/9 | 0/0 | 0/0 |   9/9 |
+--------+-----+-----+-----+-------+
cells are verified/total
```

---

## 3. Seed files confirmed present

All 9 exist, each declares a theorem whose name matches its filename, and each
carries a `Status: verified ✅` header.

| File | Bytes | Theorem | Statement |
|------|-------|---------|-----------|
| `SET/E/SET_E_01.lean` | 246 | `SET_E_01` | `A ∩ B = B ∩ A` |
| `SET/E/SET_E_02.lean` | 295 | `SET_E_02` | `A ∩ (B ∪ C) = (A ∩ B) ∪ (A ∩ C)` |
| `SET/E/SET_E_03.lean` | 246 | `SET_E_03` | subset transitivity (2-step) |
| `SET/E/SET_E_04.lean` | 271 | `SET_E_04` | subset chain (3-step) |
| `TOP/E/TOP_E_01.lean` | 347 | `TOP_E_01` | continuity via preimage-of-open |
| `TOP/E/TOP_E_02.lean` | 394 | `TOP_E_02` | composition of continuous maps |
| `ALG/E/ALG_E_01.lean` | 247 | `ALG_E_01` | `(a * b)⁻¹ = b⁻¹ * a⁻¹` |
| `ALG/E/ALG_E_02.lean` | 276 | `ALG_E_02` | homomorphisms preserve inverses |
| `ALG/E/ALG_E_03.lean` | 200 | `ALG_E_03` | `(a + b)(a - b) = a² - b²` |

`python problems/manifest.py check` reports **OK — 9 entries, no issues found**:
every file on disk has a manifest entry, and every manifest entry points at a
file that exists.

⚠️ **Scope of "confirmed":** these files were confirmed to exist, to parse, and
to match their manifest entries. They were **not** re-elaborated against Mathlib
in this pass — that needs `lake build` (Mathlib `v4.33.0-rc1`, toolchain
`leanprover/lean4:v4.33.0-rc1`). The `verified ✅` headers are inherited from the
original hand-verification.

---

## 4. Running `extract_candidates.py`

Pulls `theorem` / `lemma` declarations out of Lean files, guesses a domain and a
difficulty tier, drops anything already solved in `problems/`, and writes JSONL.
**Python 3.9+, stdlib only.**

### Bootstrap the manifest from the existing tree

```bash
python problems/extract_candidates.py --seed
```

Rebuilds `candidates.jsonl` from the 9 verified problems, every entry marked
`verified: true`. Safe to re-run; it replaces the file. Run this after
hand-editing files in the tree so the manifest catches up.

### Harvest a dataset

```bash
python problems/extract_candidates.py --source ../datasets/ProofNetSharp --append
```

| Flag | Effect |
|------|--------|
| `--source DIR` | Directory to walk recursively for `.lean` files |
| `--seed` | Rebuild from `problems/` instead of a dataset |
| `--append` | Add to `candidates.jsonl` instead of replacing it |
| `--out PATH` | Write somewhere other than `problems/candidates.jsonl` |
| `--domain X` | Force a domain on everything extracted (skip the guesser) |
| `--tier E\|M\|H` | Force a tier on everything extracted |
| `--only-domain X` | Keep only candidates classified into domain `X` |
| `--only-tier E\|M\|H` | Keep only candidates in that tier |
| `--limit N` | Stop after N new candidates |
| `--strip-proofs` | Discard source proofs, keep statements only |
| `--dry-run` | Print what would be written, write nothing |

Typical first contact with a new dataset:

```bash
# 1. look before you leap
python problems/extract_candidates.py --source ../datasets/PutnamBench --dry-run

# 2. take a bounded slice
python problems/extract_candidates.py --source ../datasets/PutnamBench \
    --append --only-domain NUM --limit 50
```

### What it writes

One JSON object per line:

```json
{"id": "3d51e7c6023f", "name": "prime_two", "domain": "NUM", "tier": "M",
 "statement": "theorem prime_two : Nat.Prime 2", "proof": "by\n  norm_num",
 "imports": ["Mathlib"], "source_dataset": "ProofNetSharp",
 "source_file": "ProofNetSharp/a.lean", "verified": false,
 "problem_path": "", "notes": ""}
```

- `id` — 12 hex chars, SHA-1 of the statement **with the declaration name
  stripped**, so the same theorem harvested from two datasets under two names
  collides and gets deduplicated.
- `statement` is split from `proof` at the first `:=` at bracket depth 0.
- Duplicates are checked both against `candidates.jsonl` and against every
  statement already in `problems/`.

### Classification heuristics

- **Domain** — weighted keyword scoring (`Matrix`/`LinearMap` → LIN,
  `IsOpen`/`nhds` → TOP, `Nat.Prime`/`ZMod` → NUM, …). Highest score wins;
  a score of 0 yields `UNK` plus a note. `UNK` entries cannot be promoted until
  you assign a domain — `manifest.py check` flags them.
- **Tier** — a recognised dataset in the path wins first (`putnam`/`imo`/
  `olympiad` → H, `proofnet`/`formalmath` → M), matching the README's source
  mapping. Otherwise: ≤4 tactics and a statement ≤200 chars → E, else M.

Both are guesses meant to be corrected. Override with `--domain` / `--tier` at
extraction time, or per-entry at promote time.

---

## 5. Using `manifest.py`

Reads and writes `candidates.jsonl`, prints ASCII tables. **Stdlib only, no
pandas.** Every command accepts `--manifest PATH` to point at a different file.

`<id>` is a full id, a **unique id prefix** (`3d51`), or a theorem name.

### `list` — what is in the manifest

```bash
python problems/manifest.py list
python problems/manifest.py list --pending --domain TOP
python problems/manifest.py list --verified --width 80
```

`--domain X` · `--tier E|M|H` · `--verified` · `--pending` · `--source SUBSTR` ·
`--limit N` · `--width N` (statement column, default 60).

```
+--------------+-----+------+----------+----------+------------------------------+
| id           | dom | tier | status   | name     | statement                    |
+--------------+-----+------+----------+----------+------------------------------+
| 69fcb424a4fd | ALG | E    | verified | ALG_E_01 | theorem ALG_E_01 {G : Type…  |
+--------------+-----+------+----------+----------+------------------------------+
```

### `stats` — coverage matrix

```bash
python problems/manifest.py stats
```

Domain × tier grid of `verified/total`, plus a count of `.lean` files on disk.
This is the "how far along is the benchmark" view.

### `show` — one entry in full

```bash
python problems/manifest.py show 3d51
```

Prints every field, then the statement and proof unwrapped.

### `promote` — write a candidate into the tree

```bash
python problems/manifest.py promote 3d51 --dry-run     # preview the file
python problems/manifest.py promote 3d51               # write it
python problems/manifest.py promote ef7d --domain CMB --tier M
```

- Allocates the next free `NN` for `{DOMAIN}/{TIER}` and writes
  `problems/{DOMAIN}/{TIER}/{DOMAIN}_{TIER}_{NN}.lean`.
- Renames the theorem to match its new id, and writes the import line, a header
  comment block, the provenance, and the manifest id into the file.
- No proof in the record → the file is emitted with `:= by\n  sorry` and you are
  told so.
- Records `problem_path` back into the manifest. **Re-promoting the same entry
  rewrites that same file** rather than allocating a second number.
- `--index N` forces a number · `--force` overwrites · `--verify` also flips the
  flag · `--domain` / `--tier` override the record.

### `verify` / `unverify` — the gate

```bash
python problems/manifest.py verify 3d51 --note "elaborates clean in VS Code"
python problems/manifest.py unverify 3d51
```

Flip `verified` after you have opened the file and confirmed it elaborates and
proves. Only verified entries should feed the harness.

### `add` — hand-written problems

```bash
python problems/manifest.py add path/to/thing.lean --domain CMB --tier M \
    --source hand-written --note "from lecture notes"
```

Extracts every declaration in the file and appends them as pending candidates,
skipping ids already in the manifest.

### `check` — consistency gate

```bash
python problems/manifest.py check
```

Exit code 0 when clean, 1 when not. Catches: unknown domain/tier, duplicate ids,
`problem_path` pointing at a missing file, entries marked verified with no file
or with a `sorry` still in the proof, and `.lean` files on disk with no manifest
entry. **Worth wiring into `.github/workflows/` as a CI step.**

### End-to-end

```bash
python problems/extract_candidates.py --source ../datasets/ProofNetSharp --append
python problems/manifest.py list --pending --domain TOP
python problems/manifest.py promote a1b2c3d4e5f6
#   ... open the generated .lean in VS Code, confirm it elaborates ...
python problems/manifest.py verify a1b2c3d4e5f6
python problems/manifest.py check
```

---

## 6. Decisions made where the spec was ambiguous

Recorded here because nothing was escalated for a ruling.

1. **Filename convention.** `README.md` gave two contradictory forms
   (`{DOMAIN}__{TIER}__{NN}.lean` and `{NN:03d}`). Chose the form the 9 seed
   files already use — `{DOMAIN}_{TIER}_{NN}.lean`, single underscore, two
   digits — and corrected the README. Renaming 9 verified files to match a
   README line would have been the more disruptive fix.

2. **Where the scripts live.** Both sit in `problems/`, next to
   `candidates.jsonl`, because `README.md` referenced `extract_candidates.py`
   with no path. They resolve paths relative to their own location, so they run
   correctly from any working directory.

3. **`candidates.jsonl` is committed, not ignored.** It is the manifest of
   record — the `verified` flags are the project's state, not build output.

4. **Seeded rather than left empty.** `candidates.jsonl` was bootstrapped from
   the 9 verified problems with `--seed`, so `manifest.py` is usable
   immediately and `check` has a baseline to compare the tree against.

5. **No datasets are vendored.** PutnamBench / ProofNetSharp / miniF2F /
   FormalMATH are not in this repo and were not downloaded. `extract_candidates.py`
   takes `--source` so you can point it at a checkout anywhere on disk; it
   infers the dataset name from the first path component under the source root.

6. **Unclassifiable candidates are kept, not dropped.** They land as `UNK` with
   a note and are blocked from promotion until you assign a domain — silently
   discarding them would hide extractor gaps.

7. **Comment stripping is naive.** `--` inside a string literal would be treated
   as a comment. Real math statements essentially never contain one; the
   simplicity was worth more than the edge case.

8. **`.gitkeep` over an empty-directory workaround.** Plain empty files, no
   content, so the 33-cell grid survives a clone.

---

## 7. Verified in this session

```
$ python problems/extract_candidates.py --seed
Seeded 9 verified entries -> C:\lean\MyMathlibProject\problems\candidates.jsonl

$ python problems/manifest.py check
OK — 9 entries, no issues found.
```

Also exercised against a synthetic dataset directory: extraction with domain
classification (TOP/NUM/LIN/UNK all correct), tier assignment from the dataset
name, duplicate rejection against the seed set, `--only-domain` filtering,
`--dry-run`, `promote` (fresh and re-promote), `show`, `list --pending`, `stats`,
`add` duplicate rejection, and `check` correctly failing on an `UNK` domain.
All test artifacts were deleted afterwards.
