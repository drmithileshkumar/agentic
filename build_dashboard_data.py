"""
build_dashboard_data.py -- merge the three analyses into one dashboard feed.

Imports analyze.py, classify_failures.py and history_analysis.py as modules and
calls their build_json() functions, so the dashboard is fed by exactly the same
code that produces the text reports -- there is no second implementation to
drift.

Usage
-----
    python build_dashboard_data.py --logs-dir C:\\lean\\logs\\20260906_191021
    python build_dashboard_data.py --logs-dir <dir> --out C:\\lean\\dashboard_data.json
    python build_dashboard_data.py --all-runs --out C:\\lean\\dashboard_data.json

With no --out, the file is written as dashboard_data.json inside the run
directory (or inside --log-base when merging every run).

Output keys
-----------
    meta              run_dir, generated_at, total_proofs, model
    pass_rate         by_tier / step_economy / tactic_freq / tactic_precision
    total_failed      modes
    repetition_rate   advanced_rate_by_step / goal_shrinkage
    proofs            one row per proof for the dashboard table

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# The three analysis scripts live beside this one.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze                # noqa: E402
import classify_failures      # noqa: E402
import history_analysis       # noqa: E402

DEFAULT_LOG_BASE = analyze.DEFAULT_LOG_BASE
THEOREM_TRUNCATE = 60


def _theorem_label(theorem: str) -> str:
    """One-line theorem text with the boilerplate import removed."""
    body = str(theorem).replace("import Mathlib", "").strip()
    return " ".join(body.split())


def proof_rows(proofs) -> list[dict]:
    rows = []
    for p in proofs:
        rows.append({
            "proof_id": p.proof_id,
            "theorem": _theorem_label(p.theorem),
            "result": p.result or "INCOMPLETE",
            "total_steps": p.total_steps,
            "dominant_outcome": p.dominant_outcome,
        })
    return rows


def model_name(dirs: list[Path]) -> str:
    models = []
    for summary in analyze.load_summaries(dirs):
        name = summary.get("model")
        if name and name not in models:
            models.append(str(name))
    return ", ".join(models) if models else "unknown"


def build(dirs: list[Path]) -> dict:
    proofs = analyze.load_proofs(dirs)
    if not proofs:
        sys.exit("no proof_*.jsonl files found in: %s"
                 % ", ".join(str(d) for d in dirs))

    # Each module reads the same files independently. That costs a little I/O
    # and keeps each script standalone, which is the point of the split.
    payload = {}
    payload.update(analyze.build_json(proofs))
    payload.update(classify_failures.build_json(
        classify_failures.load_proofs(dirs)))
    payload.update(history_analysis.build_json(
        history_analysis.load_proofs(dirs)))

    payload["meta"] = {
        "run_dir": ", ".join(str(d) for d in dirs),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_proofs": len(proofs),
        "model": model_name(dirs),
    }
    payload["proofs"] = proof_rows(proofs)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge the three analyses into dashboard_data.json.")
    parser.add_argument("--logs-dir", help="a single run directory to analyse")
    parser.add_argument("--all-runs", action="store_true",
                        help="merge every run directory under --log-base")
    parser.add_argument("--log-base", default=DEFAULT_LOG_BASE,
                        help="root holding run directories (default: %s)" % DEFAULT_LOG_BASE)
    parser.add_argument("--out",
                        help="output path (default: dashboard_data.json in the run directory)")
    args = parser.parse_args(argv)

    dirs = analyze.resolve_dirs(args)
    payload = build(dirs)

    if args.out:
        out = Path(args.out)
    elif args.all_runs:
        out = Path(args.log_base) / "dashboard_data.json"
    else:
        out = dirs[0] / "dashboard_data.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = payload["meta"]
    print("Wrote %s" % out)
    print("  proofs   : %d" % meta["total_proofs"])
    print("  proved   : %d / %d" % (payload["pass_rate"]["proved"],
                                    payload["pass_rate"]["total"]))
    print("  failed   : %d" % payload["total_failed"])
    print("  model    : %s" % meta["model"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
