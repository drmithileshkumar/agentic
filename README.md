# MyMathlibProject

A Lean 4 / Mathlib theorem-proving benchmark, organised as **11 mathematical
domains × 3 difficulty tiers**, together with a
[Lean Blueprint](https://github.com/PatrickMassot/leanblueprint) site that
publishes the problem set and a set of independent-study chapters on agentic
proof search.

| | |
|---|---|
| Toolchain | `leanprover/lean4:v4.33.0-rc1`, Mathlib `v4.33.0-rc1` |
| Problems in the manifest | 837 |
| Verified in Lean | 9 |
| Sources | [ProofNetSharp](https://huggingface.co/datasets/PAug/ProofNetSharp), [miniF2F](https://huggingface.co/datasets/cat-searcher/minif2f-lean4), hand-written seed set |

**A problem counts as verified only when it elaborates under `lake build`.**
The nine verified problems are a Lean library (`Problems` in `lakefile.toml`,
rooted at `problems/Problems.lean`) and are in the default build targets, so CI
fails if a Mathlib bump breaks one. `manifest.py check` cross-checks that index
against the manifest, so a problem cannot be marked verified while sitting
outside the build.

Everything else in the manifest is a *candidate*: a statement with no proof,
carrying neither a `\leanok` nor a Lean declaration link in the blueprint.

## Layout

```
problems/            the benchmark: manifest, tooling, verified problems
  candidates.jsonl     the manifest — one JSON object per problem
  Problems.lean        index module; what `lake build` follows
  {DOMAIN}/{TIER}/     verified problems, e.g. SET/E/SET_E_01.lean
  extract_candidates.py  harvest and classify problems
  manifest.py            browse, promote and verify entries
  gen_blueprint.py       manifest -> blueprint/src/content.tex
  test_classify.py       classification regression tests
  test_blueprint.py      LaTeX sanitizer tests
blueprint/           the Lean Blueprint site
  src/web.tex          preamble and chapter includes (hand-written)
  src/content.tex      the benchmark chapters (GENERATED — do not edit)
  src/chapters/        independent-study chapters (hand-written)
MyMathlibProject/    the Lean library proper
```

See [`problems/README.md`](problems/README.md) for the benchmark and
[`problems/SETUP_COMPLETE.md`](problems/SETUP_COMPLETE.md) for the full tool
reference.

## Getting started

```bash
lake exe cache get      # download Mathlib's build artifacts (do this first)
lake build              # builds MyMathlibProject and the verified problems
```

The benchmark tooling and its tests are stdlib-only. Two things need packages:

```bash
pip install -r requirements.txt   # to build the blueprint
pip install "datasets>=2.19"      # only to harvest new problems
```

## Working on the benchmark

```bash
python problems/manifest.py stats           # coverage matrix
python problems/manifest.py list --pending  # candidates awaiting work
python problems/manifest.py show <id>       # one entry in full
python problems/manifest.py promote <id>    # write it into problems/{DOMAIN}/{TIER}/
# ... prove it in Lean, confirm `lake build` is clean ...
python problems/manifest.py verify <id>     # flip the flag
python problems/manifest.py check           # consistency gate (CI runs this)
```

Harvesting more candidates (every mode merges; nothing is dropped without
`--replace`):

```bash
python problems/extract_candidates.py --sources proofnet,minif2f
python problems/extract_candidates.py --reclassify   # re-run classification in place
```

## Building the blueprint

```bash
python problems/gen_blueprint.py     # regenerate content.tex from the manifest
cd blueprint && leanblueprint web
cd web && python -m http.server 8080
```

`blueprint/web/` is generated and not committed; CI builds it in
`.github/workflows/blueprint.yml` and uploads it as an artifact.

Two workarounds are baked in for building without a full TeX installation —
a `kpsewhich` shim and `nonreducedgraph` (which avoids the Graphviz `tred`
binary). Both are explained in `blueprint/src/kpsewhich_shim.py` and
`blueprint/src/web.tex`; neither is needed if you have TeX Live and Graphviz.

## Known limitations

- **9 of 837 entries (1.1%) are verified.** This is a corpus with a small
  verified core, not yet a scored benchmark — there is no evaluation harness
  or baseline.
- **Coverage is uneven** and reflects the source datasets rather than a design:
  Number Theory and Algebra hold roughly half the problems between them, while
  Geometry has 3 and Probability 1. ProofNet and miniF2F simply contain almost
  no geometry or probability.
- **Tier is a proxy.** Competition problems (IMO, Putnam, AIME) are Hard by
  construction; everything else is scored on the structure of its Lean
  statement. That is a measure of how much machinery a statement carries, not
  of mathematical depth.
- `leanblueprint pdf` is not set up (no `print.tex`, no LaTeX toolchain here).
