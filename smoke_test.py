from lean_interact import LeanREPLConfig, LeanServer, LocalProject, Command, ProofStep

project = LocalProject(directory=r"C:\lean\MyMathlibProject")
config = LeanREPLConfig(project=project, verbose=True)  # first run: builds/caches the REPL binary
server = LeanServer(config)

print("=== Part 1: core Lean ProofStep loop ===")
resp = server.run(Command(cmd="theorem my_thm (x : Unit) : Nat := by sorry"))
print(resp)
ps = resp.sorries[0].proof_state

resp2 = server.run(ProofStep(tactic="apply Int.natAbs", proof_state=ps))
print(resp2)

resp3 = server.run(ProofStep(tactic="exact -37", proof_state=resp2.proof_state))
print(resp3)
assert resp3.proof_status == "Completed" and not resp3.goals, "core loop did NOT complete"
print(">>> core loop OK\n")

print("=== Part 2: Mathlib reachable through harness ===")
resp = server.run(Command(cmd="""import Mathlib
theorem ex_mathlib (x : Nat) : x + 0 = x := by sorry"""))
print(resp)
resp2 = server.run(ProofStep(tactic="simp", proof_state=resp.sorries[0].proof_state))
print(resp2)
assert resp2.proof_status == "Completed", "Mathlib proof did NOT complete"
print(">>> Mathlib loop OK")

print("\n=== SMOKE TEST PASSED ===")
