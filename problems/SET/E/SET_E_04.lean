import Mathlib

-- SET-E-04: three-step subset chain
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem SET_E_04 (A B C D : Set α) (h1 : A ⊆ B) (h2 : B ⊆ C) (h3 : C ⊆ D) : A ⊆ D := by
  intro x hx; exact h3 (h2 (h1 hx))
