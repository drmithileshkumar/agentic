"""
harness — production agent ↔ Lean 4 pipeline.

Modules:
    lean_agent    LeanAgentHarness: drives a single proof attempt
    logger        ProofLogger: structured JSONL + stats logging
    batch_runner  BatchRunner: runs many proofs and aggregates stats
"""
from .lean_agent import LeanAgentHarness, ProofResult
from .logger import ProofLogger
from .batch_runner import BatchRunner

__all__ = ["LeanAgentHarness", "ProofResult", "ProofLogger", "BatchRunner"]
