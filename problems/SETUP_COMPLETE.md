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

**Since resolved:** `MyMathlibProject/Test.lean` was leftover scaffolding
(`import Mathlib.Topology.Basic` + a bare `#check`) that nothing imported —
`MyMathlibProject.lean` imports only `Basic.lean`. It was flagged here rather
than deleted at the time, and has since been removed.

---

## 2. Folder structure

11 domains × 3 tiers = 33 directories, all present.

```
problems/
├── README.md                  domain/tier reference tables
├── SETUP_COMPLETE.md          this file
├── candidates.jsonl           the manifest (837 entries, 9 verified)
├── Problems.lean              index module — what `lake build` follows
├── extract_candidates.py      dataset → candidates.jsonl
├── manifest.py                browse / verify / promote
├── gen_blueprint.py           manifest → blueprint/src/content.tex
├── test_classify.py           classification regression tests
├── test_blueprint.py          LaTeX sanitizer tests
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

The `.lean` files above are only the *verified* problems. The other 828 entries
are candidates that live in `candidates.jsonl` and nowhere else until they are
promoted.

Coverage as reported by `python problems/manifest.py stats`:

```
+--------+-------+-------+-------+-------+
| domain |     E |     M |     H | total |
+--------+-------+-------+-------+-------+
| SET    |   4/9 |  0/13 |   0/9 |  4/31 |
| TOP    |  2/17 |  0/32 |  0/22 |  2/71 |
| ALG    |  3/86 | 0/103 |  0/13 | 3/202 |
| ABA    |  0/59 |  0/50 |   0/7 | 0/116 |
| LIN    |   0/6 |  0/19 |   0/3 |  0/28 |
| NUM    | 0/111 |  0/87 |  0/38 | 0/236 |
| RAN    |  0/14 |  0/56 |  0/45 | 0/115 |
| CAN    |   0/7 |  0/12 |   0/9 |  0/28 |
| CMB    |   0/1 |   0/4 |   0/1 |   0/6 |
| GEO    |     - |   0/1 |   0/2 |   0/3 |
| PRB    |     - |   0/1 |     - |   0/1 |
| ALL    | 9/310 | 0/378 | 0/149 | 9/837 |
+--------+-------+-------+-------+-------+
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

✅ **Scope of "confirmed":** all nine files were elaborated against Mathlib
`v4.33.0-rc1` (toolchain `leanprover/lean4:v4.33.0-rc1`) and all nine are clean.

They are no longer verified only by assertion. `problems/` is the `Problems`
Lean library in `lakefile.toml` and is in `defaultTargets`, so a plain
`lake build` elaborates every one of them and CI fails if a Mathlib bump breaks
one. Before this, the nine files sat outside the build entirely: `lake build`
and the CI workflow never touched them, and `verified ✅` in a file header was a
claim nothing checked.

---

## 4. Running `extract_candidates.py`

Pulls `theorem` / `lemma` declarations out of Lean files, guesses a domain and a
difficulty tier, drops anything already solved in `problems/`, and writes JSONL.
**Python 3.9+, stdlib only.**

> **Every mode merges.** `candidates.jsonl` is the only record of which
> problems have been promoted and verified, so no mode discards it unless you
> pass `--replace`. This was not always true: `--sources` and `--source` used to
> truncate the manifest unless given `--append`, and because the extractor also
> skips statements it finds on disk, a single run could delete the nine verified
> entries *and* refuse to re-add them.

### Refresh the seed entries from the tree

```bash
python problems/extract_candidates.py --seed
```

Re-reads the verified problems in `problems/` and marks them `verified: true`.
Harvested candidates already in the manifest are carried over untouched. Run
this after hand-editing files in the tree so the manifest catches up.

### Harvest from Hugging Face

```bash
python problems/extract_candidates.py --sources proofnet,minif2f
```

Needs `pip install datasets`. Known sources are in `DATASET_SOURCES`:
`proofnet` (`PAug/ProofNetSharp`) and `minif2f`
(`cat-searcher/minif2f-lean4`). Re-running adds only what is genuinely new —
statements already in the manifest or on disk are recognised and skipped.

### Harvest from a local checkout

```bash
python problems/extract_candidates.py --source ../datasets/ProofNetSharp
```

### Reclassify what is already there

```bash
python problems/extract_candidates.py --reclassify
python problems/extract_candidates.py --reclassify --dry-run   # report only
```

Re-runs domain and tier assignment over the manifest in place and prints a
summary of what moved. Verified entries are left alone: their domain and tier
name a directory on disk, so changing them would orphan the `.lean` file.

| Flag | Effect |
|------|--------|
| `--sources NAMES` | Comma-separated Hugging Face sources to pull |
| `--source DIR` | Directory to walk recursively for `.lean` files |
| `--seed` | Refresh seed entries from `problems/` |
| `--reclassify` | Re-run classification over the existing manifest |
| `--replace` | **Destructive.** Discard the manifest instead of merging |
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
    --only-domain NUM --limit 50
```

### What it writes

One JSON object per line:

```json
{"id": "3d51e7c6023f", "name": "prime_two", "domain": "NUM", "tier": "M",
 "statement": "theorem prime_two : Nat.Prime 2", "proof": "by\n  norm_num",
 "imports": ["Mathlib"], "source_dataset": "ProofNetSharp",
 "source_file": "PAug/ProofNetSharp#valid", "source_id": "Rudin|exercise_1_2",
 "informal": "Prove that 2 is prime.", "verified": false,
 "problem_path": "", "notes": ""}
```

`source_id` is the dataset's own identifier for the problem and `informal` is
its natural-language statement; both come from the `--sources` path and are what
the blueprint renders for unverified candidates.

- `id` — 12 hex chars, SHA-1 of the statement **with the declaration name
  stripped**, so the same theorem harvested from two datasets under two names
  collides and gets deduplicated.
- `statement` is split from `proof` at the first `:=` at bracket depth 0.
- Duplicates are checked both against `candidates.jsonl` and against every
  statement already in `problems/`.

### Classification heuristics

Both live in `classify()`, so `--source`, `--sources` and `--reclassify` cannot
drift apart. See `problems/README.md` for the full rules; the short version:

- **Domain** — weighted keyword scoring over the Lean statement and the prose,
  with the statement weighted higher. Identifiers match on token boundaries
  (`Basis` must not fire inside `IsTopologicalBasis`), the source textbook adds
  a prior, and unscored entries fall back to the ambient number type before
  `UNK`. `UNK` entries cannot be promoted — `manifest.py check` fails on them.
- **Tier** — competition sources (IMO, Putnam, AIME, USAMO) are `H` outright.
  Otherwise a short source proof means `E`, and everything else is scored on the
  structure of the statement: hypotheses and quantifiers count double,
  connectives single, plus a point per 60 characters. ≤ 6 is `E`, ≥ 16 is `H`.

Both are guesses meant to be corrected. Override with `--domain` / `--tier` at
extraction time, or per-entry at promote time. `problems/test_classify.py`
pins the cases that have been got wrong before.

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

9. **Merging is the default; destroying takes a flag.** Reversed from the
   original `--append` opt-in. The manifest holds the `verified` flags, which
   are the project's state and cannot be recomputed from anything else, so the
   safe behaviour has to be the one you get by accident.

10. **Competition problems are classified by content, not by filename.** The
    `imo_*` prefix used to force Algebra, which put 39 olympiad problems there
    regardless of subject. The prefix now only decides *tier* — a competition
    floor — while the domain is scored from the statement like everything else.

11. **`UNK` is a bug, not a bucket.** With the source prior and the number-type
    fallback in place, nothing is unclassifiable in practice, so `manifest.py
    check` treats any `UNK` as a failure rather than a to-do.

12. **The generated blueprint is not committed.** `blueprint/web/` is ~7 MB of
    HTML regenerated wholesale on every build; it is now ignored and produced by
    `.github/workflows/blueprint.yml`. `blueprint/src/content.tex` *is*
    committed — it is small, reviewable, and CI checks it against what
    `gen_blueprint.py` currently produces.

13. **Unbalanced source LaTeX is closed, not discarded.** Some dataset
    statements are truncated mid-formula. Dropping the whole statement lost more
    than emitting it with the delimiter closed.

---

## 7. Verified in this session

```
$ lake build
Build completed successfully (8672 jobs).   # includes all 9 seed problems

$ python problems/extract_candidates.py --seed
Seeded 9 verified entries -> C:\lean\MyMathlibProject\problems\candidates.jsonl
Carried over 828 harvested candidates (837 total)

$ python problems/manifest.py check
OK — 837 entries, no issues found.

$ python problems/test_classify.py
23/23 checks passed

$ python problems/test_blueprint.py
12/12 checks passed

$ python problems/gen_blueprint.py
  837 theorem blocks across 11 domains (9 verified)

$ cd blueprint && leanblueprint web
  134 pages built
```

Earlier passes also exercised, against a synthetic dataset directory: extraction
with domain classification, tier assignment, duplicate rejection against the
seed set, `--only-domain` filtering, `--dry-run`, `promote` (fresh and
re-promote), `show`, `list --pending`, `stats`, `add` duplicate rejection, and
`check` correctly failing on an `UNK` domain. All test artifacts were deleted
afterwards.
