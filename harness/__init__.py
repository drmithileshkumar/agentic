"""
harness — production agent ↔ Lean 4 pipeline.

Modules:
    lean_agent    LeanAgentHarness: drives a single proof attempt
    logger        ProofLogger: structured JSONL + stats logging
    batch_runner  BatchRunner: runs many proofs and aggregates stats
    whole_proof_agent  WholeProofAgent: one-shot whole-proof generation
    step_selector      picks proof steps to challenge and builds the prompts
    interrogator       Interrogator: challenges a verified proof step by step
    variant_generator  false theorem variants for the calibration experiment
"""
from .lean_agent import LeanAgentHarness, ProofResult
from .logger import ProofLogger
from .batch_runner import BatchRunner
from .whole_proof_agent import WholeProofAgent
from .interrogator import Interrogator

__all__ = ["LeanAgentHarness", "ProofResult", "ProofLogger", "BatchRunner",
           "WholeProofAgent", "Interrogator"]
