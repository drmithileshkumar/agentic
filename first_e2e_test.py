"""
First end-to-end test: LLM agent <-> Lean 4, via LeanInteract.
Agentic Hallucinations -- independent study.

What this proves: that every piece built so far (Lean install, Mathlib project,
LeanInteract harness, and now a real LLM agent) can actually run together as
one loop -- agent proposes a tactic, Lean checks it, result goes back to the
agent -- and that we can log every step in a shape matching 04_pipeline.md.

This is a FIRST DRAFT smoke test, not production harness code. In particular,
the branch marked "unverified" below needs a real run to confirm -- see
07_first_e2e_test.md for what to check and how to fix it if reality doesn't
match the assumption.

Requires:
    pip install lean-interact anthropic
    setx ANTHROPIC_API_KEY "sk-ant-..."     (Windows, then reopen terminal)

Run:
    python first_e2e_test.py
"""

import json
import os
from datetime import datetime, timezone

from lean_interact import LeanREPLConfig, LeanServer, LocalProject, Command, ProofStep
import anthropic

# ---- config -----------------------------------------------------------

PROJECT_DIR = r"C:\lean\MyMathlibProject"
LOG_PATH = "e2e_test_log.jsonl"
MAX_ITERATIONS = 8
MODEL = "claude-sonnet-5"

# Deliberately not one-shot-closeable by a single automation tactic (see
# SYSTEM_PROMPT below, which also bans them) so the loop actually runs more
# than one iteration and produces a real trace.
THEOREM_STATEMENT = "theorem toy_comm_assoc (a b c : Nat) : a + (b + c) = c + (b + a) := by sorry"

SYSTEM_PROMPT = """You are proving a Lean 4 theorem one tactic at a time.
You will be shown the current goal state and a history of what you've tried.
Respond with EXACTLY ONE Lean 4 tactic and nothing else -- no explanation,
no code fences, no markdown, no leading/trailing text. Just the tactic, e.g.:
rw [Nat.add_comm]

Do not use `ring`, `omega`, `simp`, `aesop`, `tauto`, or `decide` -- prove this
step by step using intro/rw/exact/apply/calc instead, so the proof trace is
actually informative for the study. If the previous step returned an error,
read it and adjust your approach on this turn."""

# ---- logging ------------------------------------------------------------

def log_event(event: dict):
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")
    print(json.dumps(event, indent=2))


# ---- agent side -----------------------------------------------------------

def get_agent_tactic(client, goal_text, history):
    history_text = "\n".join(
        f"Step {i + 1}: tried `{h['tactic']}` -> {h['outcome']}"
        + (f" -- error: {h['error']}" if h.get("error") else "")
        for i, h in enumerate(history)
    ) or "(none yet)"

    user_msg = f"""Current goal:
{goal_text}

History so far:
{history_text}

Propose the next tactic."""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text.strip().strip("`").strip()


# ---- main loop -----------------------------------------------------------

def main():
    print(f"Connecting to Lean project at {PROJECT_DIR} ...")
    project = LocalProject(directory=PROJECT_DIR)
    config = LeanREPLConfig(project=project, verbose=True)
    server = LeanServer(config)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    resp = server.run(Command(cmd=THEOREM_STATEMENT))
    if not resp.sorries:
        log_event({
            "event": "error",
            "detail": "no sorry found -- theorem statement itself failed to elaborate",
            "messages": [m.data for m in resp.messages],
        })
        return

    proof_state = resp.sorries[0].proof_state
    goal_text = resp.sorries[0].goal
    log_event({
        "event": "start", "theorem": THEOREM_STATEMENT,
        "goal": goal_text, "proof_state": proof_state,
    })

    history = []
    for step in range(1, MAX_ITERATIONS + 1):
        tactic = get_agent_tactic(client, goal_text, history)

        try:
            step_result = server.run(ProofStep(tactic=tactic, proof_state=proof_state))
        except Exception as e:
            # UNVERIFIED: assuming a rejected tactic can raise here. Run this
            # script once, see what actually happens on a bad tactic (raise,
            # vs. a normal response with an error field), and adjust this
            # except-branch to match reality -- log the difference either way.
            log_event({
                "event": "step", "step": step, "tactic": tactic,
                "outcome": "rejected", "error": str(e),
                "proof_state_before": proof_state,
            })
            history.append({"tactic": tactic, "outcome": "rejected", "error": str(e)})
            continue

        if step_result.proof_status == "Completed":
            log_event({
                "event": "step", "step": step, "tactic": tactic, "outcome": "proved",
                "proof_state_before": proof_state, "proof_state_after": step_result.proof_state,
            })
            log_event({"event": "done", "result": "PROVED", "total_steps": step})
            return

        if step_result.proof_state == proof_state:
            outcome = "no_progress_or_rejected"  # see note in 07_first_e2e_test.md
        else:
            outcome = "advanced"

        history.append({"tactic": tactic, "outcome": outcome, "error": None})
        log_event({
            "event": "step", "step": step, "tactic": tactic, "outcome": outcome,
            "proof_state_before": proof_state, "proof_state_after": step_result.proof_state,
            "goals_after": step_result.goals,
        })

        proof_state = step_result.proof_state
        goal_text = step_result.goals[0] if step_result.goals else goal_text

    log_event({"event": "done", "result": "FAILED_BUDGET_EXHAUSTED", "total_steps": MAX_ITERATIONS})


if __name__ == "__main__":
    main()
