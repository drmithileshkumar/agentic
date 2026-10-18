"""Tests for step_selector: pure functions, no stubs needed.

Run:  harness-venv\\Scripts\\python.exe harness\\tests\\test_step_selector.py
"""
import random
import sys
from collections import Counter

sys.path.insert(0, r"C:\lean")

from harness.step_selector import (  # noqa: E402
    CHALLENGE_PROMPT_TEMPLATES, CHALLENGE_TYPES, PRIORITY_TACTICS,
    assign_challenge_types, build_challenge_prompt, select_steps, _is_priority,
)


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    print("Case 0: priority detection")
    for t in ["apply h", "exact Nat.add_comm _ _", "rw [h]", "rw?", "simp only [h]",
              "have h : P := by simp", "obtain ⟨a, b⟩ := h", "  exact h"]:
        check("priority: %r" % t, _is_priority(t))
    for t in ["simp", "simp [h]", "simplify", "rwa [h]", "omega", "applying h", "exacto", ""]:
        check("not priority: %r" % t, not _is_priority(t))

    print("Case 1: select_steps basics")
    tactics = ["intro h", "rw [h]", "omega", "simp", "exact h"]
    out = select_steps(tactics, max_steps=3, rng=random.Random(0))
    check("length 3", len(out) == 3, out)
    check("ascending", out == sorted(out), out)
    check("last index never chosen", 4 not in out, out)
    check("priority step included", 1 in out, out)

    print("Case 2: priority first, then random fill")
    tactics = ["omega", "rw [a]", "ring", "exact b", "norm_num", "rfl"]
    seen = Counter()
    for seed in range(50):
        out = select_steps(tactics, max_steps=3, rng=random.Random(seed))
        check_ok = len(out) == 3 and {1, 3} <= set(out) and 5 not in out and out == sorted(out)
        if not check_ok:
            failures.append("seed %d: %s" % (seed, out))
        seen.update(set(out) - {1, 3})
    check("every run: both priority steps + one other, no closing step",
          not [f for f in failures if f.startswith("seed")])
    check("random fill touches every non-priority step over 50 seeds",
          set(seen) == {0, 2, 4}, dict(seen))

    print("Case 3: too many priority steps -> sampled, still ascending")
    tactics = ["apply a", "apply b", "apply c", "apply d", "apply e", "rfl"]
    picks = Counter()
    for seed in range(40):
        out = select_steps(tactics, max_steps=2, rng=random.Random(seed))
        if len(out) != 2 or out != sorted(out) or 5 in out:
            failures.append("overflow seed %d: %s" % (seed, out))
        picks.update(out)
    check("every overflow run valid", not [f for f in failures if f.startswith("overflow")])
    check("sampling spreads over priority steps", len(picks) >= 4, dict(picks))

    print("Case 4: blanks and edge cases")
    check("blank tactics skipped",
          select_steps(["", "   ", "rw [h]", "\t", "rfl"], 3, rng=random.Random(1)) == [2])
    check("fewer eligible than max_steps -> all of them",
          select_steps(["intro", "omega", "rfl"], 3, rng=random.Random(1)) == [0, 1])
    check("single-step proof -> []", select_steps(["rfl"], 3) == [])
    check("empty proof -> []", select_steps([], 3) == [])
    check("max_steps 0 -> []", select_steps(["a", "b", "c"], 0) == [])
    check("default rng works", len(select_steps(["intro", "omega", "rfl"])) == 2)

    print("Case 5: assign_challenge_types")
    steps = [0, 2, 5]
    a = assign_challenge_types(steps, "proof-A")
    b = assign_challenge_types(steps, "proof-A")
    check("keys are the step indices", list(a) == steps, a)
    check("deterministic for the same proof_id", a == b)
    check("values are challenge types", set(a.values()) <= set(CHALLENGE_TYPES), a)
    vals = list(a.values())
    k = CHALLENGE_TYPES.index(vals[0])
    check("rotates consecutively",
          vals == [CHALLENGE_TYPES[(k + i) % 4] for i in range(3)], vals)
    firsts = Counter(assign_challenge_types([0], "proof-%d" % i)[0] for i in range(400))
    check("starting type varies across proof ids", len(firsts) == 4, dict(firsts))
    check("roughly even across corpus", min(firsts.values()) > 60, dict(firsts))
    check("four steps cover all four types",
          set(assign_challenge_types([0, 1, 2, 3], "x").values()) == set(CHALLENGE_TYPES))
    check("empty selection -> {}", assign_challenge_types([], "x") == {})

    print("Case 6: build_challenge_prompt")
    body = "intro h\nrw [h]\nrfl"
    p = build_challenge_prompt(body, 1, "rw [h]", "redundancy_claim")
    expected = ("Here is your proof:\n\n" + body + "\n\n"
                + CHALLENGE_PROMPT_TEMPLATES["redundancy_claim"].format(step_num=2, tactic="rw [h]"))
    check("exact format", p == expected, repr(p))
    check("step number is 1-based", "step 2 ('rw [h]')" in p, p)
    for ct in CHALLENGE_TYPES:
        q = build_challenge_prompt(body, 0, "intro h", ct)
        check("renders %s" % ct, "step 1" in q and "intro h" in q and "{" not in q, q)
    try:
        build_challenge_prompt(body, 0, "x", "bogus")
        check("unknown type raises ValueError", False)
    except ValueError:
        check("unknown type raises ValueError", True)
    check("tactic with braces is safe",
          "{h}" in build_challenge_prompt(body, 0, "simp [{h}]", "authoritative_denial"))

    print()
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
