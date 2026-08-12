import Mathlib

-- SET-E-01: intersection is commutative
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem SET_E_01 (A B : Set α) : A ∩ B = B ∩ A := by
  ext x; simp only [Set.mem_inter_iff]; tauto
