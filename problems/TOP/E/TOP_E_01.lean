import Mathlib

-- TOP-E-01: continuity via preimage-of-open-is-open
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem TOP_E_01 {α β : Type*} [TopologicalSpace α] [TopologicalSpace β]
    (f : α → β) (h : ∀ U, IsOpen U → IsOpen (f ⁻¹' U)) : Continuous f := by
  exact continuous_def.mpr h
