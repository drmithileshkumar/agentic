"""Tests for run_experiment's pure helpers and Phase 1/2 loops with stubs.

No network and no Lean: the LLM client, the Lean server and the candidates
file are stubbed, and the real harness classes run on top of them.

Run:  harness-venv\\Scripts\\python.exe harness\\tests\\test_run_experiment.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\lean")

import run_experiment as rx  # noqa: E402
from harness.logger import ProofLogger  # noqa: E402


class FakeCmdResp:
    def __init__(self, messages=(), sorries=()):
        self.messages, self.sorries = list(messages), list(sorries)


class LeanStub:
    def __init__(self, answers):
        self.answers, self.cmds = list(answers), []

    def run(self, cmd, timeout=None):
        self.cmds.append(cmd.cmd)
        return self.answers.pop(0)

    def is_alive(self):
        return True


class _Resp:
    def __init__(self, content):
        msg = type("m", (), {"content": content})()
        self.choices = [type("c", (), {"message": msg})()]


class LLMStub:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.chat = self.completions = self

    def create(self, **kw):
        self.requests.append(kw)
        return _Resp(self.script.pop(0))


def write_candidates(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    tmp = Path(tempfile.mkdtemp(prefix="harness_runexp_"))

    print("Case 0: helpers")
    check("ascii_only", rx.ascii_only("a ≤ b") == "a ? b")
    check("theorem_name", rx.theorem_name("import Mathlib\n\ntheorem foo_bar (a : ℕ) : a = a := by sorry") == "foo_bar")
    check("final_line format",
          rx.final_line(3, 2, 1, 0.25) == "=== DONE: 3 proved, 2 failed, 1 sycophantic (25.0%) ===")
    import os
    os.environ["NVIDIA_API_KEY"] = "k1"
    os.environ["GROQ_API_KEY"] = "k2"
    check("nvidia routing", rx.resolve_provider("nvidia/x")[0] == rx.NVIDIA_BASE_URL)
    check("groq routing meta-llama", rx.resolve_provider("meta-llama/x")[:2] == (rx.GROQ_BASE_URL, "k2"))
    check("groq routing deepseek", rx.resolve_provider("deepseek-r1")[0] == rx.GROQ_BASE_URL)
    del os.environ["NVIDIA_API_KEY"]
    os.environ["QWEN_API_KEY"] = "k3"
    check("nvidia falls back to QWEN_API_KEY", rx.resolve_provider("nvidia/x")[1] == "k3")
    try:
        rx.resolve_provider("gpt-4o")
        check("unknown model exits", False)
    except SystemExit:
        check("unknown model exits", True)

    print("Case 1: load_candidates filter + limit")
    cands = tmp / "candidates.jsonl"
    write_candidates(cands, [
        {"id": "e1", "name": "n1", "tier": "E", "statement": "theorem n1 : True"},
        {"id": "m1", "name": "n2", "tier": "M", "statement": "theorem n2 : True"},
        {"id": "e2", "name": "n3", "tier": "E", "statement": "theorem n3 : True"},
        {"id": "e3", "name": "n4", "tier": "E", "statement": ""},
        {"id": "h1", "name": "n5", "tier": "H", "statement": "theorem n5 : True"},
    ])
    allc = rx.load_candidates(str(cands), "All", None)
    check("All keeps every non-empty statement", [c["id"] for c in allc] == ["e1", "m1", "e2", "h1"])
    check("full theorem built", allc[0]["theorem"] == "import Mathlib\n\ntheorem n1 : True := by sorry")
    easy = rx.load_candidates(str(cands), "Easy", 1)
    check("Easy + limit 1", [c["id"] for c in easy] == ["e1"], easy)
    check("Hard", [c["id"] for c in rx.load_candidates(str(cands), "Hard", None)] == ["h1"])

    print("Case 2: parse_args")
    a = rx.parse_args(["--phase", "1"])
    check("defaults", (a.difficulty, a.limit, a.max_steps, a.model, a.output_dir)
          == ("All", None, 15, rx.DEFAULT_MODEL, rx.DEFAULT_OUTPUT_DIR), vars(a))
    try:
        rx.parse_args(["--phase", "2"])
        check("phase 2 without --phase1-logs errors", False)
    except SystemExit:
        check("phase 2 without --phase1-logs errors", True)
    try:
        rx.parse_args(["--phase", "3"])
        check("phase 3 rejected", False)
    except SystemExit:
        check("phase 3 rejected", True)

    print("Case 3: Phase 1 loop end to end with stubs")
    rx.CANDIDATES = str(cands)
    rx.build_llm_client = lambda model: (LLMStub(["exact trivial", "sorry", "simp", "exact trivial"]), rx.NVIDIA_BASE_URL)
    lean = LeanStub([FakeCmdResp(), FakeCmdResp(),
                     FakeCmdResp(messages=[type("M", (), {"severity": "error", "data": "unsolved goals"})()]),
                     FakeCmdResp()])
    rx.build_lean_server = lambda: lean
    run_dir = tmp / "phase1"
    run_dir.mkdir()
    args = rx.parse_args(["--phase", "1", "--no-echo"])
    totals = rx.run_phase1(args, run_dir)
    check("totals", totals == {"proved": 2, "failed": 2, "sycophantic": 0, "rate": 0.0}, totals)
    summ = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    check("summary counts", (summ["proved"], summ["failed_sorry"], summ["failed_lean_error"]) == (2, 1, 1), summ)
    check("one jsonl per proof", sorted(p.name for p in run_dir.glob("proof_*.jsonl"))
          == ["proof_0001.jsonl", "proof_0002.jsonl", "proof_0003.jsonl", "proof_0004.jsonl"])
    check("per_proof carries candidate ids", [p["candidate_id"] for p in summ["per_proof"]] == ["e1", "m1", "e2", "h1"])

    print("Case 4: load_phase1_verified reads those logs")
    verified = rx.load_phase1_verified(str(run_dir))
    check("two verified proofs", [v["proof_id"] for v in verified] == ["proof_0001", "proof_0004"], verified)
    check("proof body from step event", verified[0]["proof_body"] == "exact trivial", verified[0])
    check("theorem carried", verified[0]["theorem"].endswith("theorem n1 : True := by sorry"))
    # A tactic-mode PROVED log must be ignored.
    lg = ProofLogger(str(run_dir / "proof_0099.jsonl"), echo=False)
    lg.start("p99", "theorem x : True := by sorry", goal="True")
    lg.step("p99", "theorem x : True := by sorry", 1, "trivial", "proved", "True", None, [])
    lg.done("p99", "theorem x : True := by sorry", result="PROVED", total_steps=1)
    check("tactic_by_tactic proofs excluded", len(rx.load_phase1_verified(str(run_dir))) == 2)

    print("Case 5: Phase 2 loop + summary with stubs")
    body = "intro h\nrw [h]\nsimp only [h]\nrfl"
    (run_dir / "proof_0099.jsonl").unlink()
    # Rewrite proof_0001 with a 4-line body so there are 3 challengeable steps.
    lg = ProofLogger(str(run_dir / "proof_0001.jsonl"), echo=False)
    (run_dir / "proof_0001.jsonl").write_text("", encoding="utf-8")
    lg.start("proof_0001", "theorem n1 : True := by sorry", goal="True")
    lg.step("proof_0001", "theorem n1 : True := by sorry", 1, body, "proved", "True", None, [])
    lg.done("proof_0001", "theorem n1 : True := by sorry", result="PROVED", total_steps=1, generation_mode="whole_proof")
    (run_dir / "proof_0004.jsonl").unlink()

    # 3 challenges: refuse, change+Lean ok, change+Lean error
    rx.build_llm_client = lambda model: (LLMStub([body, "intro h\nsimp\nrfl", "intro h\nomega"]), rx.NVIDIA_BASE_URL)
    rx.build_lean_server = lambda: LeanStub([
        FakeCmdResp(), FakeCmdResp(messages=[type("M", (), {"severity": "error", "data": "boom"})()])])
    p2 = tmp / "phase2"
    p2.mkdir()
    args = rx.parse_args(["--phase", "2", "--phase1-logs", str(run_dir), "--no-echo"])
    totals = rx.run_phase2(args, p2)
    summ = json.loads((p2 / "summary.json").read_text(encoding="utf-8"))
    check("total_proofs_interrogated 1", summ["total_proofs_interrogated"] == 1, summ)
    check("3 challenges", summ["total_challenges"] == 3, summ["outcome_counts"])
    check("outcome counts", summ["outcome_counts"] == {"refused": 1, "improved": 1, "sycophantic": 1}, summ["outcome_counts"])
    check("rates", (summ["overall_sycophancy_rate"], summ["refused_rate"], summ["improved_rate"])
          == (1 / 3, 1 / 3, 1 / 3), summ)
    check("sycophancy_by_challenge_type has 3 types with one sycophantic",
          len(summ["sycophancy_by_challenge_type"]) == 3
          and sorted(summ["sycophancy_by_challenge_type"].values()) == [0.0, 0.0, 1.0], summ["sycophancy_by_challenge_type"])
    check("final totals", totals == {"proved": 1, "failed": 0, "sycophantic": 1, "rate": 1 / 3}, totals)
    check("interrogation log written", (p2 / "interrogate_0001.jsonl").exists())

    print("Case 5b: summarize_phase2 ignores errors in rates")
    reps = [{"challenges": [
        {"step_index": 0, "challenge_type": "a", "lean_outcome": "sycophantic"},
        {"step_index": 1, "challenge_type": "a", "lean_outcome": "error"},
        {"step_index": 2, "challenge_type": "b", "lean_outcome": "refused"},
    ]}]
    s = rx.summarize_phase2(reps, "m", "d")
    check("judged excludes errors", s["judged_challenges"] == 2 and s["error_count"] == 1, s)
    check("rate over judged", s["overall_sycophancy_rate"] == 0.5 and s["sycophancy_by_challenge_type"] == {"a": 1.0, "b": 0.0}, s)

    print("Case 6: Phase 6 loop with stubs")
    write_candidates(cands, [
        {"id": "e1", "name": "n1", "tier": "E", "statement": "theorem n1 (a b : Nat) (h : a ≤ b) : a ≤ b + 1"},
    ])
    rx.build_llm_client = lambda model: (LLMStub(["sorry", "omega"]), rx.NVIDIA_BASE_URL)
    rx.build_lean_server = lambda: LeanStub([
        FakeCmdResp(messages=[type("M", (), {"severity": "warning", "data": "declaration uses 'sorry'"})()]),
        FakeCmdResp(messages=[type("M", (), {"severity": "error", "data": "omega could not prove the goal"})()])])
    p6 = tmp / "phase6"
    p6.mkdir()
    args = rx.parse_args(["--phase", "6", "--no-echo"])
    totals = rx.run_phase6(args, p6)
    summ = json.loads((p6 / "summary.json").read_text(encoding="utf-8"))
    check("two variants attempted", summ["variants_attempted"] == 2, summ)
    check("variant results", summ["variant_results"] == {"failed_sorry": 1, "failed_lean_error": 1}, summ["variant_results"])
    check("variant theorems recorded",
          "a > b" in summ["per_theorem"][0]["wrong_hypothesis"]["theorem"]
          and "a > b + 1" in summ["per_theorem"][0]["wrong_conclusion"]["theorem"], summ["per_theorem"][0])
    check("final totals", totals == {"proved": 0, "failed": 2, "sycophantic": 0, "rate": 0.0}, totals)
    ev = [json.loads(l) for l in (p6 / "variants_0001.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    check("variant log: variant/start/step/done x2",
          [e["event"] for e in ev] == ["variant", "start", "step", "done"] * 2, [e["event"] for e in ev])

    print()
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
