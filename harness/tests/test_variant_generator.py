"""Tests for variant_generator: pure string rewriting plus a stubbed agent.

Run:  harness-venv\\Scripts\\python.exe harness\\tests\\test_variant_generator.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.logger import ProofLogger  # noqa: E402
from harness.variant_generator import generate_variants, run_variant_experiment  # noqa: E402

H = "import Mathlib\n\n"
T = " := by sorry"


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    def wh(stmt):
        return generate_variants(stmt)["wrong_hypothesis"]

    def wc(stmt):
        return generate_variants(stmt)["wrong_conclusion"]

    print("Case 1: wrong_hypothesis ladder")
    s = H + "theorem t1 (a b : ℕ) (h : a ≤ b) (h2 : b ≤ 5) : a ≤ 5" + T
    check("≤ -> > (first only, hypotheses only)",
          wh(s) == H + "theorem t1 (a b : ℕ) (h : a > b) (h2 : b ≤ 5) : a ≤ 5" + T, wh(s))
    s = H + "theorem t2 (a b : ℕ) (h : a = b) : a + 1 = b + 1" + T
    check("= -> ≠ (binder colons untouched)",
          wh(s) == H + "theorem t2 (a b : ℕ) (h : a ≠ b) : a + 1 = b + 1" + T, wh(s))
    s = H + "theorem t3 (n : ℕ) (h : Even n) : Even (n * 3)" + T
    check("Even -> Odd in hypothesis only",
          wh(s) == H + "theorem t3 (n : ℕ) (h : Odd n) : Even (n * 3)" + T, wh(s))
    s = H + "theorem t4 (n : ℕ) (h : Odd n) : Odd (n ^ 2)" + T
    check("Odd -> Even", wh(s) == H + "theorem t4 (n : ℕ) (h : Even n) : Odd (n ^ 2)" + T, wh(s))
    s = H + "theorem t5 (a b : ℕ) : a + b = b + a" + T
    check("nothing to flip -> (h_false : False) added before the colon",
          wh(s) == H + "theorem t5 (a b : ℕ) (h_false : False) : a + b = b + a" + T, wh(s))
    s = H + "theorem t6 : 1 + 1 = 2" + T
    check("no binders at all -> h_false added",
          wh(s) == H + "theorem t6 (h_false : False) : 1 + 1 = 2" + T, wh(s))
    s = H + "theorem t7 (p q : Prop) (hp : p) (hq : q) : p ∧ q" + T
    check("Prop hypotheses without flippable ops -> h_false",
          wh(s) == H + "theorem t7 (p q : Prop) (hp : p) (hq : q) (h_false : False) : p ∧ q" + T, wh(s))
    s = H + "theorem t8 (a : ℕ) (h : a ≥ 2) : a ≠ 0" + T
    check("≥ is not ≤; ≠ is not =; -> h_false",
          wh(s) == H + "theorem t8 (a : ℕ) (h : a ≥ 2) (h_false : False) : a ≠ 0" + T, wh(s))

    print("Case 2: wrong_conclusion ladder")
    s = H + "theorem c1 (a b : ℕ) (h : a = b) : a + 1 = b + 1" + T
    check("= -> ≠ in conclusion only",
          wc(s) == H + "theorem c1 (a b : ℕ) (h : a = b) : a + 1 ≠ b + 1" + T, wc(s))
    s = H + "theorem c2 (a : ℕ) (h : a = 1) : a ≤ 2" + T
    check("≤ -> >", wc(s) == H + "theorem c2 (a : ℕ) (h : a = 1) : a > 2" + T, wc(s))
    s = H + "theorem c3 (a b : ℕ) : a + b ≥ a" + T
    check("+ -> -", wc(s) == H + "theorem c3 (a b : ℕ) : a - b ≥ a" + T, wc(s))
    s = H + "theorem c4 : True" + T
    check("True -> False", wc(s) == H + "theorem c4 : False" + T, wc(s))
    s = H + "theorem c5 (p : Prop) (hp : p) : p ∨ ¬ p" + T
    check("else: negate", wc(s) == H + "theorem c5 (p : Prop) (hp : p) : ¬ (p ∨ ¬ p)" + T, wc(s))
    s = H + "theorem c6 : ∀ x : ℕ, x + 0 = x" + T
    check("quantifier colon inside conclusion is not the split point",
          wc(s) == H + "theorem c6 : ∀ x : ℕ, x + 0 ≠ x" + T, wc(s))
    check("…and its hypothesis variant keeps the conclusion intact",
          wh(s) == H + "theorem c6 (h_false : False) : ∀ x : ℕ, x + 0 = x" + T, wh(s))
    s = H + "theorem c7 (s t : Set ℕ) : s ∩ t = t ∩ s" + T
    check("set equality", wc(s) == H + "theorem c7 (s t : Set ℕ) : s ∩ t ≠ t ∩ s" + T, wc(s))

    print("Case 3: shapes and failures")
    s = "theorem nohdr (a : ℕ) : a = a := by\n  sorry"
    v = generate_variants(s)
    check("`:= by\\n  sorry` tail preserved",
          v["wrong_conclusion"] == "theorem nohdr (a : ℕ) : a ≠ a := by\n  sorry", v)
    s = "lemma l1 {G : Type*} [Group G] (a : G) : a * 1 = a" + T
    check("lemma with implicit/instance binders",
          wc(s) == "lemma l1 {G : Type*} [Group G] (a : G) : a * 1 ≠ a" + T, wc(s))
    s = "example (a : ℕ) : a ≤ a + 1" + T
    check("example", wc(s) == "example (a : ℕ) : a > a + 1" + T, wc(s))
    v = generate_variants("theorem t (a : ℕ) : a = a")
    check("no sorry tail -> both None", v == {"wrong_hypothesis": None, "wrong_conclusion": None}, v)
    v = generate_variants("def foo : ℕ := 3 := by sorry")
    check("no theorem keyword -> both None", v == {"wrong_hypothesis": None, "wrong_conclusion": None}, v)
    v = generate_variants("theorem t (a : ℕ) := by sorry")
    check("no top-level colon -> both None", v == {"wrong_hypothesis": None, "wrong_conclusion": None}, v)
    s = H + "theorem nested (f : ℕ → ℕ) (h : ∀ x, f x ≤ x) : f 0 ≤ 0" + T
    check("colon-free nested hypothesis still parses",
          wh(s) == H + "theorem nested (f : ℕ → ℕ) (h : ∀ x, f x > x) : f 0 ≤ 0" + T, wh(s))
    check("variants always differ from the original",
          all(x != s for x in generate_variants(s).values()))

    print("Case 4: run_variant_experiment")
    tmp = tempfile.mkdtemp(prefix="harness_variants_")
    log = Path(tmp) / "v.jsonl"
    logger = ProofLogger(str(log), echo=False)

    class AgentStub:
        def __init__(self):
            self.calls = []

        def attempt_proof(self, theorem, proof_id):
            self.calls.append((theorem, proof_id))
            logger.start(proof_id, theorem, goal=theorem)
            logger.done(proof_id, theorem, result="FAILED_SORRY", total_steps=1,
                        generation_mode="whole_proof")
            return {"proof_id": proof_id, "result": "failed_sorry"}

    agent = AgentStub()
    s = H + "theorem e1 (a b : ℕ) (h : a ≤ b) : a ≤ b + 1" + T
    out = run_variant_experiment(s, "proof_0007", agent, logger)
    check("base id echoed", out["base_proof_id"] == "proof_0007", out)
    check("both variants attempted with _wh/_wc ids",
          [c[1] for c in agent.calls] == ["proof_0007_wh", "proof_0007_wc"], agent.calls)
    check("variant theorems passed to the agent",
          agent.calls[0][0] == out["wrong_hypothesis"]["theorem"]
          and agent.calls[1][0] == out["wrong_conclusion"]["theorem"])
    check("results carried", out["wrong_hypothesis"]["result"]["proof_id"] == "proof_0007_wh"
          and out["wrong_conclusion"]["result"]["result"] == "failed_sorry", out)
    ev = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    check("variant event precedes each agent run",
          [e["event"] for e in ev] == ["variant", "start", "done"] * 2, [e["event"] for e in ev])
    check("variant event links ids and statements",
          ev[0]["proof_id"] == "proof_0007_wh" and ev[0]["base_proof_id"] == "proof_0007"
          and ev[0]["variant_kind"] == "wrong_hypothesis" and ev[0]["original_theorem"] == s
          and ev[0]["variant_theorem"] == out["wrong_hypothesis"]["theorem"], ev[0])

    print("Case 4b: unparseable statement -> None entries, agent untouched")
    agent = AgentStub()
    out = run_variant_experiment("not a theorem", "proof_0008", agent, logger)
    check("both None", out["wrong_hypothesis"] == {"theorem": None, "result": None}
          and out["wrong_conclusion"] == {"theorem": None, "result": None}, out)
    check("agent not called", agent.calls == [])

    print()
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
