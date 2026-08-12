import Mathlib

-- SET-E-03: subset is transitive
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem SET_E_03 (A B C : Set α) (h1 : A ⊆ B) (h2 : B ⊆ C) : A ⊆ C := by
  intro x hx; exact h2 (h1 hx)
