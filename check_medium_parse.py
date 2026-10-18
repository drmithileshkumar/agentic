"""
Pre-flight check: which Medium-tier statements fail to elaborate?

A statement that doesn't elaborate still yields a `sorry` proof state -- Lean
replaces the broken type with `sorry`, so the agent sees `⊢ sorry` and burns
its whole budget on a goal that isn't the theorem (exercise_2_2_9 in the
20261006_173553 run). This script finds those before a full run.

Uses the same lean_interact setup as harness/lean_agent.py, with one change:
Mathlib is imported once and every statement is elaborated against that env,
instead of re-importing Mathlib inside each command (~230s each in the harness).
Elaboration of the statement itself is unaffected.

Results are checkpointed to medium_parse_checkpoint.jsonl, so a killed run
resumes where it stopped.

    harness-venv\\Scripts\\python.exe check_medium_parse.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

from lean_interact import Command, LeanREPLConfig, LeanServer, LocalProject
from lean_interact.interface import LeanError

PROJECT_DIR = r"C:\lean\MyMathlibProject"
CANDIDATES  = r"C:\lean\MyMathlibProject\problems\candidates.jsonl"
# Per-tier outputs; Medium keeps its original file names.
OUTPUTS = {
    "M": (Path(r"C:\lean\medium_parse_failures.txt"), Path(r"C:\lean\medium_parse_checkpoint.jsonl")),
    "H": (Path(r"C:\lean\hard_parse_failures.txt"),   Path(r"C:\lean\hard_parse_checkpoint.jsonl")),
    "E": (Path(r"C:\lean\easy_parse_failures.txt"),   Path(r"C:\lean\easy_parse_checkpoint.jsonl")),
}

IMPORT_TIMEOUT_SECONDS = 900
STATEMENT_TIMEOUT_SECONDS = 120

SORRY_WORD = re.compile(r"\bsorry\b")


def load_tier(tier: str) -> list[dict]:
    entries = []
    with open(CANDIDATES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                if e.get("tier") == tier and e.get("statement", "").strip():
                    entries.append(e)
    return entries


def load_checkpoint(checkpoint: Path) -> dict[str, dict]:
    done = {}
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r
    return done


def check(server: LeanServer, env: int, stmt: str) -> tuple[bool, str]:
    """Return (flagged, reason)."""
    try:
        resp = server.run(Command(cmd=f"{stmt} := by sorry", env=env),
                          timeout=STATEMENT_TIMEOUT_SECONDS)
    except Exception as exc:
        return True, f"exception: {type(exc).__name__}: {exc}"

    if isinstance(resp, LeanError):
        return True, f"LeanError: {resp.message}"

    errors = [m.data for m in resp.messages or [] if m.severity == "error"]
    if not resp.sorries:
        return True, "no sorry/proof state; " + (" | ".join(errors) or "no messages")

    goal = resp.sorries[0].goal
    if SORRY_WORD.search(goal):
        return True, "goal contains sorry: " + (" | ".join(errors) or goal)
    if errors:
        return True, "elaboration errors: " + " | ".join(errors)
    return False, ""


def start_server() -> tuple[LeanServer, int]:
    config = LeanREPLConfig(project=LocalProject(directory=PROJECT_DIR), verbose=False)
    server = LeanServer(config)
    print("Importing Mathlib (several minutes)...", flush=True)
    resp = server.run(Command(cmd="import Mathlib"), timeout=IMPORT_TIMEOUT_SECONDS)
    if isinstance(resp, LeanError):
        sys.exit(f"import Mathlib failed: {resp.message}")
    return server, resp.env


def main():
    parser = argparse.ArgumentParser(description="Find statements that fail to elaborate")
    parser.add_argument("--tier", choices=sorted(OUTPUTS), default="M")
    args = parser.parse_args()
    out_path, checkpoint = OUTPUTS[args.tier]

    entries = load_tier(args.tier)
    done = load_checkpoint(checkpoint)
    todo = [e for e in entries if e["id"] not in done]
    print(f"{len(entries)} tier-{args.tier} theorems; {len(done)} already checked, {len(todo)} to go",
          flush=True)

    if todo:
        server, env = start_server()
        with checkpoint.open("a", encoding="utf-8") as ckpt:
            for i, e in enumerate(todo, 1):
                flagged, reason = check(server, env, e["statement"])
                if reason.startswith("exception"):
                    # A timeout kills the REPL -- rebuild it for the next statement.
                    server, env = start_server()
                rec = {"id": e["id"], "name": e.get("name", ""), "flagged": flagged,
                       "reason": reason}
                ckpt.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ckpt.flush()
                done[e["id"]] = rec
                print(f"[{i}/{len(todo)}] {e.get('name', e['id'])}: "
                      f"{'FLAGGED' if flagged else 'ok'}", flush=True)

    by_id = {e["id"]: e for e in entries}
    flagged = [r for r in done.values() if r["flagged"] and r["id"] in by_id]
    with out_path.open("w", encoding="utf-8") as out:
        for r in flagged:
            e = by_id[r["id"]]
            out.write(f"=== {r['id']}  {e.get('name', '')}  [{e.get('domain', '')}]\n")
            out.write(f"reason: {r['reason']}\n")
            out.write(e["statement"] + "\n\n")

    checked = sum(1 for i in by_id if i in done)
    print(f"\nTotal checked: {checked}/{len(entries)}")
    print(f"Total flagged: {len(flagged)}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
