import Mathlib

-- TOP-E-02: composition of continuous functions is continuous
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem TOP_E_02 {α β γ : Type*} [TopologicalSpace α] [TopologicalSpace β] [TopologicalSpace γ]
    (f : α → β) (g : β → γ) (hf : Continuous f) (hg : Continuous g) :
    Continuous (g ∘ f) := by
  exact hg.comp hf
