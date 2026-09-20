import Mathlib

-- LIN-E-01: exercise_1_3
-- Source: ProofNetSharp (PAug/ProofNetSharp#valid)
-- Difficulty: E
-- Status: verified ✅
-- Manifest id: f93079d7a77b

theorem LIN_E_01 {F V : Type*} [AddCommGroup V] [Field F]
  [Module F V] {v : V} : -(-v) = v := by
  rw [neg_neg]
