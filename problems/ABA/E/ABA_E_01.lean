import Mathlib

-- ABA-E-01: exercise_7_1_11
-- Source: ProofNetSharp (PAug/ProofNetSharp#valid)
-- Difficulty: E
-- Status: verified ✅
-- Manifest id: 21632a0498ad

theorem ABA_E_01 {R : Type*} [CommRing R] [IsDomain R]
  {x : R} (hx : x^2 = 1) : x = 1 ∨ x = -1 := by
  exact (sq_eq_one_iff.mp hx)
