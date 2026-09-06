"""Mock tests for _extract_tactic.

A reasoning model answers with a paragraph, not a tactic. The 20260906_164928
smoke run sent one straight to Lean and burned a step on `unknown tactic`, with
the completion truncated mid-thought at "Thus, the next tactic". _extract_tactic
is what pulls the tactic back out of that prose.

Contract under test:
  _extract_tactic(raw) -> the line to hand Lean
  scans bottom-up, since a reasoning model states its answer last
  falls back to the last non-empty line when nothing looks like a tactic
  returns "" only for input that carries no content at all
  never returns None, never raises
"""
import sys

sys.path.insert(0, r"C:\lean")

from harness.lean_agent import TACTIC_KEYWORDS, _extract_tactic, _is_tactic_line


# The verbatim step-1 completion from logs/20260906_164928/proof_0001.jsonl.
NEMOTRON_TRUNCATED = (
    "We are proving that addition of natural numbers is commutative, i.e., "
    "`a + b = b + a` for all `a, b : \u2115`. \n"
    "In Lean 4, this is a basic property of natural numbers, and it can be "
    "directly derived from the commutativity of addition (`Nat.add_comm`). \n\n"
    "The most straightforward tactic to use here is `rw [Nat.add_comm]`, which "
    "will rewrite the goal using the commutativity of addition. \n\n"
    "Alternatively, we could also use `simp [Nat.add_comm]`, but `rw` is more "
    "direct in this case since the goal is exactly the statement of "
    "`Nat.add_comm`. \n\nThus, the next tactic"
)


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    def eq(name, raw, want):
        got = _extract_tactic(raw)
        check(name, got == want, "got %r want %r" % (got, want))

    # --------------------------------------------------------------- case 1
    print("Case 1: prose paragraph ending in a tactic line -> the tactic line")
    eq("reasoning then tactic on its own line",
       "We need to prove commutativity of addition.\n"
       "The obvious lemma is Nat.add_comm.\n"
       "rw [Nat.add_comm]",
       "rw [Nat.add_comm]")
    eq("blank lines between prose and tactic",
       "First, note the goal is symmetric.\n\n"
       "Therefore the tactic is:\n\n"
       "exact Nat.add_comm _ _",
       "exact Nat.add_comm _ _")
    eq("trailing blank lines ignored",
       "Some reasoning here about the goal.\nomega\n\n\n",
       "omega")
    eq("wordy tactic rescued by its keyword",
       "We finish by rewriting.\nrw [Nat.add_comm, Nat.mul_comm, Nat.add_assoc]",
       "rw [Nat.add_comm, Nat.mul_comm, Nat.add_assoc]")
    eq("prose after the tactic is skipped",
       "apply Nat.le_of_lt\nThis should close the goal.",
       "apply Nat.le_of_lt")

    # --------------------------------------------------------------- case 2
    print("Case 2: a single bare tactic is returned unchanged")
    for tactic in ("simp", "ring", "omega", "rfl", "decide", "linarith",
                   "norm_num", "trivial", "aesop",
                   "rw [Nat.add_comm]", "exact Nat.add_comm _ _",
                   "intro h", "by_contra h", "obtain ⟨a, b⟩ := h",
                   "field_simp", "push_neg at h",
                   "have h : a = b := by simp"):
        eq("bare %r" % tactic, tactic, tactic)
    eq("surrounding whitespace stripped", "   simp   ", "simp")
    eq("inline backticks stripped", "`rw [Nat.add_comm]`", "rw [Nat.add_comm]")

    # --------------------------------------------------------------- case 3
    print("Case 3: truncated mid-sentence, no tactic line -> last non-empty line")
    eq("the real Nemotron truncation", NEMOTRON_TRUNCATED, "Thus, the next tactic")
    eq("single truncated sentence",
       "The most straightforward approach here would be to",
       "The most straightforward approach here would be to")
    eq("all-prose paragraph",
       "This goal is symmetric.\nIt should follow from commutativity.",
       "It should follow from commutativity.")
    check("fallback is never empty for non-empty input",
          _extract_tactic(NEMOTRON_TRUNCATED) != "")

    # --------------------------------------------------------------- case 4
    print("Case 4: content-free input -> empty string")
    for raw in ("", "   ", "\n\n", "\t\n  \n", "```", "```lean\n```",
                "`", "``", "  ``  ", "```\n\n```"):
        eq("empty for %r" % raw, raw, "")
    check("None-safe", _extract_tactic(None) == "")

    # --------------------------------------------------------------- case 5
    print("Case 5: code fences are stripped, the tactic inside survives")
    eq("```lean fence", "```lean\nrw [Nat.add_comm]\n```", "rw [Nat.add_comm]")
    eq("bare fence", "```\nsimp\n```", "simp")
    eq("fence after prose",
       "Here is the tactic:\n```lean\nexact Nat.add_comm _ _\n```",
       "exact Nat.add_comm _ _")
    eq("fence with language tag and trailing prose",
       "```lean\nomega\n```\nThat should do it.",
       "omega")
    eq("fenced multi-line block takes the last tactic line",
       "```lean\nintro h\nsimp [h]\n```",
       "simp [h]")

    # --------------------------------------------------------------- case 6
    print("Case 6: invariants that must hold for any input")
    samples = [NEMOTRON_TRUNCATED, "", "   ", "simp", "```lean\nsimp\n```",
               "a\nb\nc", "Word " * 200, "x" * 500, "\n".join("line%d" % i for i in range(50))]
    for raw in samples:
        got = _extract_tactic(raw)
        check("returns str for %r" % raw[:24], isinstance(got, str), type(got).__name__)
        check("no surrounding whitespace for %r" % raw[:24], got == got.strip(), repr(got))
        check("empty only if input has no content for %r" % raw[:24],
              bool(got) or not raw.strip().strip("`").strip(), repr(got))

    # --------------------------------------------------------------- case 7
    print("Case 7: a keyword head is necessary but not sufficient")
    # Narration about a tactic must not be mistaken for the tactic. Each of
    # these has a real tactic on an earlier line, so a rejected narration line
    # has somewhere to fall through to -- proving it was rejected, not merely
    # returned by the last-line fallback.
    eq("`simp will rewrite ...` is not a tactic line",
       "rw [Nat.add_comm]\nsimp will rewrite the goal as x in A and x in B",
       "rw [Nat.add_comm]")
    eq("`exact because ...` is not a tactic line",
       "omega\nexact because the term has type Nat and we need Int",
       "omega")
    eq("`rw the goal` is not a tactic line",
       "ring\nrw the goal",
       "ring")
    eq("`apply the lemma` is not a tactic line",
       "simp\napply the lemma from before",
       "simp")

    print("Case 7b: genuine calls still accepted after the keyword")
    eq("bracket after keyword",
       "Some reasoning.\nrw [Nat.add_comm]", "rw [Nat.add_comm]")
    eq("end of line after keyword",
       "Some reasoning.\nomega", "omega")
    eq("dot-path after keyword -- uppercase but a Lean identifier",
       "Some reasoning.\napply Nat.le_of_lt", "apply Nat.le_of_lt")
    eq("dotted term after keyword",
       "Some reasoning.\nexact h.symm", "exact h.symm")
    eq("single-letter identifier after keyword",
       "Some reasoning.\nintro h", "intro h")
    eq("`have` with a binder",
       "Some reasoning.\nhave h : a = b := by simp", "have h : a = b := by simp")
    eq("Lean connective `only`",
       "Some reasoning.\nsimp only [Set.mem_inter_iff]",
       "simp only [Set.mem_inter_iff]")
    eq("Lean connective `at`",
       "Some reasoning.\npush_neg at h", "push_neg at h")
    eq("Lean connective `using`",
       "Some reasoning.\ninduction n using Nat.strong_induction_on",
       "induction n using Nat.strong_induction_on")
    eq("digit after keyword", "Some reasoning.\ncongr 1", "congr 1")
    eq("anonymous constructor after keyword",
       "Some reasoning.\nrefine ⟨_, _⟩", "refine ⟨_, _⟩")
    eq("underscore args after keyword",
       "Some reasoning.\nexact Nat.add_comm _ _", "exact Nat.add_comm _ _")

    print("Case 7c: the proof_0004 step 3 regression")
    # A backtick left glued to the head is what let a whole sentence through:
    # `simp` will rewrite ... -> head "simp", so the line was accepted outright.
    eq("backticked keyword opening a sentence is rejected",
       "ext x\n`simp` will rewrite `x ∈ A ∩ B` as `x ∈ A ∧ x ∈ B`, "
       "which is true by commutativity of `∧`.",
       "ext x")
    check("the real step-3 line is not a tactic line",
          not _is_tactic_line(
              "simp` will rewrite `x ∈ A ∩ B` as `x ∈ A ∧ x ∈ B`."))

    print("Case 6b: every declared keyword is actually recognised")
    for kw in sorted(TACTIC_KEYWORDS):
        got = _extract_tactic("Reasoning that runs on and on about the goal.\n%s foo bar baz" % kw)
        check("keyword %s survives a wordy tail" % kw,
              got == "%s foo bar baz" % kw, got)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
