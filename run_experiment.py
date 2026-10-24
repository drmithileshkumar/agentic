"""
run_experiment.py -- entry point for every phase of the experiment.

Phases
------
  0   tactic-by-tactic baseline: BatchRunner + LeanAgentHarness, exactly as
      run_proofs.py runs them
  1   whole-proof generation: WholeProofAgent.attempt_proof per theorem
  2   adversarial robustness: load the proofs Phase 1 got Lean to accept
      (--phase1-logs) and have the Interrogator challenge each one
  6   calibration: for each theorem, prove-attempt two deliberately false
      variants (variant_generator.run_variant_experiment)

Usage
-----
    .\\harness-venv\\Scripts\\Activate.ps1
    python run_experiment.py --phase 1 --difficulty Easy --limit 20
    python run_experiment.py --phase 2 --phase1-logs logs/phase1_20261018_120000
    python run_experiment.py --phase 6 --limit 10 --model meta-llama/llama-4-scout-17b-16e-instruct

Output
------
    <output-dir>/phase<N>_<timestamp>/proof_NNNN.jsonl       one JSONL per proof
    <output-dir>/phase<N>_<timestamp>/summary.json           aggregate stats

Console output is ASCII-only so it survives a cp1252 terminal; the JSONL
files carry the full Unicode theorem text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Every path derives from this file's location, so the same checkout runs
# unchanged on the Windows workstation and on the HPC (~/agentic).
ROOT         = Path(__file__).resolve().parent
LOG_DIR      = ROOT / "logs"
ENV_FILE     = ROOT / ".env"
PROJECT_DIR  = ROOT / "MyMathlibProject"
CANDIDATES   = ROOT / "MyMathlibProject" / "problems" / "candidates.jsonl"
DEFAULT_OUTPUT_DIR = str(LOG_DIR)      # argparse default; LOG_DIR is the Path
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
DEFAULT_MAX_STEPS = 15

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
GROQ_BASE_URL   = "https://api.groq.com/openai/v1"

TIER_MAP = {"Easy": "E", "Medium": "M", "Hard": "H"}

# Abort a phase after this many consecutive theorems whose LLM calls failed;
# same reasoning as BatchRunner.MAX_CONSECUTIVE_PROOF_LLM_FAILURES.
MAX_CONSECUTIVE_THEOREM_LLM_FAILURES = 5

PHASE1_TAGS = {
    "proved":            "[PROVED]",
    "failed_lean_error": "[FAILED_LEAN]",
    "failed_sorry":      "[FAILED_SORRY]",
    "failed_llm_error":  "[FAILED_LLM]",
}
PHASE2_TAGS = {
    "sycophantic": "[SYCOPHANTIC]",
    "robust":      "[ROBUST]",
    "refused":     "[REFUSED]",
    "improved":    "[IMPROVED]",
    "error":       "[ERROR]",
}

_NAME_RE = re.compile(r"\b(?:theorem|lemma)\s+(\S+)")


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def ascii_only(text: str) -> str:
    """Console-safe rendering of *text*: non-ASCII characters become '?'."""
    return text.encode("ascii", errors="replace").decode("ascii")


def theorem_name(theorem: str) -> str:
    m = _NAME_RE.search(theorem)
    return m.group(1) if m else theorem.strip().splitlines()[-1][:40]


def resolve_provider(model: str) -> tuple[str, str, str]:
    """(base_url, api_key, provider_label) for *model*, or exit with a message."""
    if model.startswith("nvidia/"):
        # The project's .env names the NIM key QWEN_API_KEY (see run_proofs.py);
        # NVIDIA_API_KEY is preferred when both are present.
        key = (os.environ.get("NVIDIA_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip()
        if not key:
            sys.exit("ERROR: --model %s needs NVIDIA_API_KEY (or QWEN_API_KEY) in %s"
                     % (model, ENV_FILE))
        return NVIDIA_BASE_URL, key, "NVIDIA NIM"
    if model.startswith("meta-llama/") or model.startswith("deepseek"):
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            sys.exit("ERROR: --model %s needs GROQ_API_KEY in %s" % (model, ENV_FILE))
        return GROQ_BASE_URL, key, "Groq"
    sys.exit("ERROR: no provider routing for --model %s "
             "(expected nvidia/*, meta-llama/* or deepseek*)" % model)


def load_candidates(path: str, difficulty: str, limit: int | None) -> list[dict]:
    """Entries from candidates.jsonl with a full `import Mathlib ... := by sorry` theorem."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"ERROR: candidates.jsonl not found at {path}")
    tier = TIER_MAP.get(difficulty)   # None for "All"

    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if tier and e.get("tier") != tier:
                continue
            stmt = (e.get("statement") or "").strip()
            if not stmt:
                continue
            out.append({
                "id":      e.get("id"),
                "name":    e.get("name") or theorem_name(stmt),
                "tier":    e.get("tier"),
                "theorem": f"import Mathlib\n\n{stmt} := by sorry",
            })
            if limit is not None and len(out) >= limit:
                break
    return out


def load_phase1_verified(log_dir: str) -> list[dict]:
    """Proofs Phase 1 got Lean to accept, from every .jsonl under *log_dir*.

    A proof qualifies when its done event has result PROVED (any case) and
    generation_mode "whole_proof". The proof body is the tactic field of its
    step event, which is where WholeProofAgent records what Lean checked.
    """
    root = Path(log_dir)
    if not root.is_dir():
        sys.exit(f"ERROR: --phase1-logs directory not found: {log_dir}")

    verified = []
    for path in sorted(root.glob("*.jsonl")):
        by_proof: dict[str, dict] = defaultdict(dict)
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = ev.get("proof_id")
                if ev.get("event") == "step" and ev.get("outcome") == "proved":
                    by_proof[pid]["proof_body"] = ev.get("tactic")
                    by_proof[pid]["theorem"] = ev.get("theorem")
                elif ev.get("event") == "done":
                    by_proof[pid]["done"] = ev
        for pid, rec in by_proof.items():
            done = rec.get("done")
            if not done:
                continue
            if str(done.get("result", "")).lower() != "proved":
                continue
            if done.get("generation_mode") != "whole_proof":
                continue
            body = rec.get("proof_body")
            if not body:
                continue
            verified.append({
                "proof_id":    pid,
                "theorem":     rec.get("theorem") or done.get("theorem"),
                "proof_body":  body,
                "source_file": str(path),
            })
    return verified


def write_summary(run_dir: Path, summary: dict) -> Path:
    out = run_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Summary written to {out}")
    return out


def final_line(proved: int, failed: int, sycophantic: int, rate: float) -> str:
    return f"=== DONE: {proved} proved, {failed} failed, {sycophantic} sycophantic ({rate * 100:.1f}%) ==="


def pin_lean_toolchain() -> None:
    """Make every `lake`/`lean` call in this process use the project's toolchain.

    lean_interact checks `lake --version` without a working directory, so
    elan resolves its *default* toolchain rather than the project's pinned
    one. The default is "stable", which moves with each Lean release: once it
    pointed at a version not installed here, elan tried to download it on
    every run and the harness died before its first LLM call. ELAN_TOOLCHAIN
    overrides that resolution process-wide. An explicit value in the
    environment is left alone.
    """
    if os.environ.get("ELAN_TOOLCHAIN"):
        return
    pin_file = Path(PROJECT_DIR) / "lean-toolchain"
    try:
        pin = pin_file.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if pin:
        os.environ["ELAN_TOOLCHAIN"] = pin
        print(f"[lean] toolchain pinned to {pin}")


def build_lean_server():
    from lean_interact import LeanREPLConfig, LeanServer, LocalProject
    print("Starting Lean server (first `import Mathlib` takes several minutes)...")
    t0 = time.perf_counter()
    server = LeanServer(LeanREPLConfig(project=LocalProject(directory=str(PROJECT_DIR)), verbose=False))
    print(f"Lean server ready in {time.perf_counter() - t0:.1f}s\n")
    return server


def build_llm_client(model: str):
    import openai
    base_url, api_key, label = resolve_provider(model)
    print(f"[provider] {label}  model={model}")
    return openai.OpenAI(api_key=api_key, base_url=base_url), base_url


def max_tokens_for(base_url: str) -> int:
    from harness.lean_agent import _resolve_max_tokens
    return _resolve_max_tokens(base_url)


# ---------------------------------------------------------------------------
# Phase 0 -- tactic-by-tactic baseline
# ---------------------------------------------------------------------------

def run_phase0(args, run_dir: Path) -> dict:
    base_url, api_key, label = resolve_provider(args.model)
    # LeanAgentHarness reads its provider from these two.
    os.environ["QWEN_API_KEY"]  = api_key
    os.environ["QWEN_BASE_URL"] = base_url
    print(f"[provider] {label}  model={args.model}")

    from harness import BatchRunner

    candidates = load_candidates(CANDIDATES, args.difficulty, args.limit)
    if not candidates:
        sys.exit("No theorems to run. Check the difficulty filter.")
    print(f"Loaded {len(candidates)} theorems")

    runner = BatchRunner(
        project_dir=str(PROJECT_DIR), log_dir=str(run_dir), model=args.model,
        max_steps=args.max_steps, echo=not args.no_echo,
    )
    results = runner.run([c["theorem"] for c in candidates])
    proved = sum(1 for r in results if r.proved)
    return {"proved": proved, "failed": len(results) - proved, "sycophantic": 0, "rate": 0.0}


# ---------------------------------------------------------------------------
# Phase 1 -- whole-proof generation
# ---------------------------------------------------------------------------

def run_phase1(args, run_dir: Path) -> dict:
    from harness import ProofLogger, WholeProofAgent
    from harness.whole_proof_agent import LLMCircuitBreakerError

    candidates = load_candidates(CANDIDATES, args.difficulty, args.limit)
    if not candidates:
        sys.exit("No theorems to run. Check the difficulty filter.")
    print(f"Loaded {len(candidates)} theorems")

    client, base_url = build_llm_client(args.model)
    server = build_lean_server()
    agent = WholeProofAgent(client, server, ProofLogger(str(run_dir / "proof_0000.jsonl"), echo=False),
                            model_name=args.model, max_tokens=max_tokens_for(base_url))

    per_proof = []
    consecutive_llm_failures = 0
    total = len(candidates)
    for idx, cand in enumerate(candidates, start=1):
        proof_id = f"proof_{idx:04d}"
        agent.logger = ProofLogger(str(run_dir / f"{proof_id}.jsonl"), echo=not args.no_echo)
        print(f"\n[{idx}/{total}] {proof_id}  {ascii_only(cand['name'])}  (tier {cand['tier']})")

        t0 = time.perf_counter()
        try:
            result = agent.attempt_proof(cand["theorem"], proof_id)
        except LLMCircuitBreakerError as exc:
            result = exc.result
        elapsed = time.perf_counter() - t0

        tag = PHASE1_TAGS.get(result["result"], "[%s]" % result["result"].upper())
        print(f"  {tag}  llm={result['llm_calls']}  time={elapsed:.1f}s")
        if result["result"] != "proved" and result["lean_messages"]:
            print(f"    lean: {ascii_only(result['lean_messages'][0])[:160]}")

        per_proof.append({
            "proof_id":      proof_id,
            "candidate_id":  cand["id"],
            "name":          cand["name"],
            "tier":          cand["tier"],
            "result":        result["result"],
            "llm_calls":     result["llm_calls"],
            "proof_body":    result["proof_body"],
            "lean_messages": result["lean_messages"],
            "elapsed_s":     round(elapsed, 1),
        })

        consecutive_llm_failures = consecutive_llm_failures + 1 if result["result"] == "failed_llm_error" else 0
        if consecutive_llm_failures >= MAX_CONSECUTIVE_THEOREM_LLM_FAILURES:
            print(f"\nBatch aborted: {consecutive_llm_failures} consecutive LLM failures -- "
                  f"endpoint likely down. {total - idx} theorem(s) not attempted.")
            break

    counts = Counter(p["result"] for p in per_proof)
    n = len(per_proof)
    summary = {
        "phase":            1,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "model":            args.model,
        "difficulty":       args.difficulty,
        "total_theorems":   n,
        "attempted_of":     total,
        "proved":           counts["proved"],
        "failed_sorry":     counts["failed_sorry"],
        "failed_lean_error": counts["failed_lean_error"],
        "failed_llm_error": counts["failed_llm_error"],
        "pass_rate":        counts["proved"] / n if n else 0.0,
        "avg_llm_calls":    sum(p["llm_calls"] for p in per_proof) / n if n else 0.0,
        "per_proof":        per_proof,
    }
    write_summary(run_dir, summary)
    return {"proved": counts["proved"], "failed": n - counts["proved"], "sycophantic": 0, "rate": 0.0}


# ---------------------------------------------------------------------------
# Phase 2 -- adversarial robustness
# ---------------------------------------------------------------------------

def summarize_phase2(reports: list[dict], model: str, phase1_logs: str) -> dict:
    """Aggregate Interrogator reports into the Phase 2 summary.

    Rates are over *judged* challenges -- those with an outcome other than
    "error" -- so an endpoint failure lowers the sample, not the rate.
    """
    outcomes_by_type: dict[str, Counter] = defaultdict(Counter)
    overall = Counter()
    for rep in reports:
        for ch in rep["challenges"]:
            outcomes_by_type[ch["challenge_type"]][ch["lean_outcome"]] += 1
            overall[ch["lean_outcome"]] += 1

    judged = sum(v for k, v in overall.items() if k != "error")

    def rate(c: Counter, key: str) -> float:
        j = sum(v for k, v in c.items() if k != "error")
        return c[key] / j if j else 0.0

    return {
        "phase":                      2,
        "timestamp":                  datetime.now(timezone.utc).isoformat(),
        "model":                      model,
        "phase1_logs":                phase1_logs,
        "total_proofs_interrogated":  len(reports),
        "total_challenges":           sum(overall.values()),
        "judged_challenges":          judged,
        "overall_sycophancy_rate":    rate(overall, "sycophantic"),
        "refused_rate":               rate(overall, "refused"),
        "improved_rate":              rate(overall, "improved"),
        "robust_rate":                rate(overall, "robust"),
        "error_count":                overall["error"],
        "outcome_counts":             dict(overall),
        "sycophancy_by_challenge_type": {
            t: rate(c, "sycophantic") for t, c in sorted(outcomes_by_type.items())
        },
        "outcomes_by_challenge_type": {t: dict(c) for t, c in sorted(outcomes_by_type.items())},
        "per_proof":                  reports,
    }


def run_phase2(args, run_dir: Path) -> dict:
    from harness import Interrogator, ProofLogger

    verified = load_phase1_verified(args.phase1_logs)
    if args.limit is not None:
        verified = verified[:args.limit]
    if not verified:
        sys.exit(f"No verified whole_proof proofs found under {args.phase1_logs}")
    print(f"Loaded {len(verified)} verified proofs from {args.phase1_logs}")

    client, base_url = build_llm_client(args.model)
    server = build_lean_server()
    interrogator = Interrogator(client, server, ProofLogger(str(run_dir / "interrogate_0000.jsonl"), echo=False),
                                model_name=args.model, max_tokens=max_tokens_for(base_url))

    reports = []
    total = len(verified)
    for idx, v in enumerate(verified, start=1):
        interrogator.logger = ProofLogger(str(run_dir / f"interrogate_{idx:04d}.jsonl"),
                                          echo=not args.no_echo)
        print(f"\n[{idx}/{total}] {v['proof_id']}  {ascii_only(theorem_name(v['theorem']))}")
        t0 = time.perf_counter()
        rep = interrogator.interrogate(v["proof_id"], v["theorem"], v["proof_body"])
        elapsed = time.perf_counter() - t0
        for ch in rep["challenges"]:
            tag = PHASE2_TAGS.get(ch["lean_outcome"], "[%s]" % ch["lean_outcome"].upper())
            print(f"  step {ch['step_index'] + 1:<3} {ch['challenge_type']:<22} {tag}")
        if not rep["challenges"]:
            print("  (no challengeable steps)")
        print(f"  sycophantic={rep['sycophancy_count']} refused={rep['refused_count']} "
              f"improved={rep['improved_count']} error={rep['error_count']}  time={elapsed:.1f}s")
        rep["theorem_name"] = theorem_name(v["theorem"])
        rep["source_file"] = v["source_file"]
        rep["elapsed_s"] = round(elapsed, 1)
        reports.append(rep)

    summary = summarize_phase2(reports, args.model, args.phase1_logs)
    write_summary(run_dir, summary)
    return {"proved": len(reports), "failed": 0,
            "sycophantic": summary["outcome_counts"].get("sycophantic", 0),
            "rate": summary["overall_sycophancy_rate"]}


# ---------------------------------------------------------------------------
# Phase 6 -- calibration on false variants
# ---------------------------------------------------------------------------

def run_phase6(args, run_dir: Path) -> dict:
    from harness import ProofLogger, WholeProofAgent
    from harness.variant_generator import VARIANT_KINDS, run_variant_experiment
    from harness.whole_proof_agent import LLMCircuitBreakerError

    candidates = load_candidates(CANDIDATES, args.difficulty, args.limit)
    if not candidates:
        sys.exit("No theorems to run. Check the difficulty filter.")
    print(f"Loaded {len(candidates)} theorems")

    client, base_url = build_llm_client(args.model)
    server = build_lean_server()
    agent = WholeProofAgent(client, server, ProofLogger(str(run_dir / "variants_0000.jsonl"), echo=False),
                            model_name=args.model, max_tokens=max_tokens_for(base_url))

    per_theorem = []
    variant_results: Counter = Counter()
    consecutive_llm_failures = 0
    total = len(candidates)
    for idx, cand in enumerate(candidates, start=1):
        base_id = f"proof_{idx:04d}"
        logger = ProofLogger(str(run_dir / f"variants_{idx:04d}.jsonl"), echo=not args.no_echo)
        agent.logger = logger
        print(f"\n[{idx}/{total}] {base_id}  {ascii_only(cand['name'])}  (tier {cand['tier']})")

        t0 = time.perf_counter()
        try:
            report = run_variant_experiment(cand["theorem"], base_id, agent, logger)
            breaker = False
        except LLMCircuitBreakerError as exc:
            # The variant that tripped it is in exc.result; the other, if it
            # had not run yet, is recorded as not attempted.
            report = {"base_proof_id": base_id}
            for kind in VARIANT_KINDS:
                report[kind] = {"theorem": None, "result": None}
            kind = next((k for k in VARIANT_KINDS
                         if exc.result["proof_id"].endswith({"wrong_hypothesis": "_wh",
                                                             "wrong_conclusion": "_wc"}[k])), None)
            if kind:
                report[kind] = {"theorem": exc.result["theorem"], "result": exc.result}
            breaker = True
        elapsed = time.perf_counter() - t0

        llm_failed_here = False
        for kind in VARIANT_KINDS:
            entry = report[kind]
            if entry["theorem"] is None:
                print(f"  {kind:<17} [NO_VARIANT]")
                variant_results["no_variant"] += 1
                continue
            if entry["result"] is None:
                print(f"  {kind:<17} [NOT_ATTEMPTED]")
                variant_results["not_attempted"] += 1
                continue
            r = entry["result"]["result"]
            tag = PHASE1_TAGS.get(r, "[%s]" % r.upper())
            print(f"  {kind:<17} {tag}  llm={entry['result']['llm_calls']}")
            variant_results[r] += 1
            llm_failed_here = llm_failed_here or r == "failed_llm_error"
        print(f"  time={elapsed:.1f}s")

        per_theorem.append({
            "base_proof_id": base_id,
            "candidate_id":  cand["id"],
            "name":          cand["name"],
            "tier":          cand["tier"],
            "original":      cand["theorem"],
            "wrong_hypothesis": report["wrong_hypothesis"],
            "wrong_conclusion": report["wrong_conclusion"],
            "elapsed_s":     round(elapsed, 1),
        })

        consecutive_llm_failures = consecutive_llm_failures + 1 if (llm_failed_here or breaker) else 0
        if consecutive_llm_failures >= MAX_CONSECUTIVE_THEOREM_LLM_FAILURES:
            print(f"\nBatch aborted: {consecutive_llm_failures} consecutive LLM failures -- "
                  f"endpoint likely down. {total - idx} theorem(s) not attempted.")
            break

    attempted = sum(v for k, v in variant_results.items() if k not in ("no_variant", "not_attempted"))
    # A false theorem that came back "proved" is the calibration failure the
    # phase exists to measure: Lean accepted a proof of something untrue only
    # if the variant is not actually false (e.g. a vacuous h_false), so these
    # need a look by hand.
    summary = {
        "phase":               6,
        "timestamp":           datetime.now(timezone.utc).isoformat(),
        "model":               args.model,
        "difficulty":          args.difficulty,
        "total_theorems":      len(per_theorem),
        "attempted_of":        total,
        "variants_attempted":  attempted,
        "variant_results":     dict(variant_results),
        "false_variant_proved_rate": variant_results["proved"] / attempted if attempted else 0.0,
        "false_variant_sorry_rate":  variant_results["failed_sorry"] / attempted if attempted else 0.0,
        "per_theorem":         per_theorem,
    }
    write_summary(run_dir, summary)
    return {"proved": variant_results["proved"], "failed": attempted - variant_results["proved"],
            "sycophantic": 0, "rate": 0.0}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run one phase of the experiment")
    parser.add_argument("--phase", type=int, required=True, choices=[0, 1, 2, 6])
    parser.add_argument("--difficulty", type=str, default="All",
                        choices=["Easy", "Medium", "Hard", "All"])
    parser.add_argument("--limit", type=int, default=None,
                        help="Max theorems (phases 0/1/6) or verified proofs (phase 2)")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                        help="Tactic budget per proof (Phase 0 only)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help="nvidia/* -> NVIDIA NIM; meta-llama/* or deepseek* -> Groq")
    parser.add_argument("--phase1-logs", type=str, default=None,
                        help="Phase 1 run directory to interrogate (required for --phase 2)")
    parser.add_argument("--no-echo", action="store_true",
                        help="Do not print each JSONL event to stdout")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    if args.phase == 2 and not args.phase1_logs:
        parser.error("--phase1-logs is required when --phase=2")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    load_dotenv(ENV_FILE, override=False)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    pin_lean_toolchain()
    # The env var only affects interpreters started after this one; this
    # process's stdout has to be reconfigured directly.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    run_ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)      # LOG_DIR unless --output-dir given
    run_dir = out_root / f"phase{args.phase}_{run_ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Phase {args.phase}  output: {run_dir}")

    runner = {0: run_phase0, 1: run_phase1, 2: run_phase2, 6: run_phase6}[args.phase]
    totals = runner(args, run_dir)

    print()
    print(final_line(totals["proved"], totals["failed"], totals["sycophantic"], totals["rate"]))
    print(f"Logs at {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
