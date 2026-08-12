import Mathlib

-- SET-E-02: intersection distributes over union
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem SET_E_02 (A B C : Set α) : A ∩ (B ∪ C) = (A ∩ B) ∪ (A ∩ C) := by
  ext x; simp only [Set.mem_inter_iff, Set.mem_union]; tauto
