import Mathlib

-- ALG-E-02: homomorphisms preserve inverses
-- Source: hand-verified (seed set)
-- Difficulty: E (Easy)
-- Status: verified ✅

theorem ALG_E_02 {G H : Type*} [Group G] [Group H] (f : G →* H) (a : G) :
    f (a⁻¹) = (f a)⁻¹ := by
  exact map_inv f a
