import Mathlib

-- ALG-E-03: ring identity
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem ALG_E_03 (a b : Int) : (a + b) * (a - b) = a^2 - b^2 := by ring
