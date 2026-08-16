/-
The verified problems of the benchmark, as a Lean library.

This file is the root module of the `Problems` library in `lakefile.toml`, and
`Problems` is a default target — so `lake build` elaborates every problem listed
here and CI fails if a Mathlib bump breaks one. A problem marked
`verified: true` in `problems/candidates.jsonl` must appear below;
`python problems/manifest.py check` enforces that in both directions.

Add an import when you promote and prove a new problem. Keep the list grouped by
domain and ordered, so the diff on a new problem is one line.
-/

-- Set Theory
import SET.E.SET_E_01
import SET.E.SET_E_02
import SET.E.SET_E_03
import SET.E.SET_E_04

-- Topology
import TOP.E.TOP_E_01
import TOP.E.TOP_E_02

-- Algebra
import ALG.E.ALG_E_01
import ALG.E.ALG_E_02
import ALG.E.ALG_E_03
