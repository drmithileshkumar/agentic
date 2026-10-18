"""
variant_generator -- subtly false versions of a true theorem, for RQ6.

The calibration experiment asks whether the model notices when a theorem is
wrong. For each theorem in the corpus two variants are made, each one small
edit away from the original:

  wrong_hypothesis   one hypothesis is flipped (≤ to >, = to ≠, Even to Odd,
                     Odd to Even) or, if none can be, `(h_false : False)` is
                     added as an extra hypothesis -- which makes the theorem
                     vacuously true, so a model that proves it by `h_false.elim`
                     is right, and one that reproduces the original proof
                     unchanged has not read the statement
  wrong_conclusion   the conclusion is flipped (= to ≠, ≤ to >, + to -, True to
                     False) or negated outright

A variant is then handed to the whole-proof agent exactly like a real theorem,
and what it does with an unprovable statement -- produce a `sorry`, produce
something Lean rejects, or produce a wrong proof it insists is fine -- is the
measurement.

Parsing is deliberately shallow. The statement is split at its top-level ` : `
(the first one at bracket depth 0 after the declaration name, so binders'
own colons and `∀ x : ℕ` inside the conclusion are skipped), and each
rewrite is applied to one side only. Anything that does not fit that shape
yields None rather than a variant that might not even parse.
"""

from __future__ import annotations

import re
from typing import Optional

from .logger import ProofLogger

VARIANT_KINDS = ("wrong_hypothesis", "wrong_conclusion")
PROOF_ID_SUFFIX = {"wrong_hypothesis": "_wh", "wrong_conclusion": "_wc"}

_TAIL_RE = re.compile(r"(\s*:=\s*by\s+sorry\s*)$")
_DECL_RE = re.compile(r"\b(theorem|lemma)\s+\S+|\bexample\b")

_OPEN  = "([{⟨"
_CLOSE = ")]}⟩"


class _Parsed:
    """The pieces of `<prefix> <binders> : <conclusion><tail>`."""

    def __init__(self, prefix: str, binders: str, conclusion: str, tail: str):
        self.prefix, self.binders, self.conclusion, self.tail = prefix, binders, conclusion, tail

    def rebuild(self, binders: Optional[str] = None, conclusion: Optional[str] = None) -> str:
        b = self.binders if binders is None else binders
        c = self.conclusion if conclusion is None else conclusion
        return f"{self.prefix}{b} : {c}{self.tail}"


def _top_level_colon(text: str, start: int) -> int:
    """Index of the first ` : ` at bracket depth 0 at or after *start*, or -1."""
    depth = 0
    i = start
    while i < len(text) - 2:
        ch = text[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(" : ", i):
            return i
        i += 1
    return -1


def _parse(theorem_statement: str) -> Optional[_Parsed]:
    tail_match = _TAIL_RE.search(theorem_statement)
    if not tail_match:
        return None
    body = theorem_statement[:tail_match.start()]
    tail = tail_match.group(1)

    decl = _DECL_RE.search(body)
    if not decl:
        return None
    colon = _top_level_colon(body, decl.end())
    if colon < 0:
        return None

    prefix     = body[:decl.end()]
    binders    = body[decl.end():colon]
    conclusion = body[colon + 3:].strip()
    if not conclusion:
        return None
    return _Parsed(prefix, binders, conclusion, tail)


# ---------------------------------------------------------------------------
# Rewrites
# ---------------------------------------------------------------------------

_ISOLATED_EQ_RE = re.compile(r"(?<![:≠<>≤≥=]) = (?![=>])")
_EVEN_RE = re.compile(r"\bEven\b")
_ODD_RE  = re.compile(r"\bOdd\b")
_TRUE_RE = re.compile(r"\bTrue\b")


def _has_parenthesised_hypothesis(binders: str) -> bool:
    return "(" in binders and ")" in binders


def _wrong_hypothesis(p: _Parsed) -> Optional[str]:
    """Flip the first hypothesis that can be flipped, else add a false one."""
    b = p.binders
    if _has_parenthesised_hypothesis(b):
        if "≤" in b:
            return p.rebuild(binders=b.replace("≤", ">", 1))
        if _ISOLATED_EQ_RE.search(b):
            return p.rebuild(binders=_ISOLATED_EQ_RE.sub(" ≠ ", b, count=1))
        if _EVEN_RE.search(b):
            return p.rebuild(binders=_EVEN_RE.sub("Odd", b, count=1))
        if _ODD_RE.search(b):
            return p.rebuild(binders=_ODD_RE.sub("Even", b, count=1))
    # Nothing to flip: add a hypothesis that can never be supplied. It goes
    # with the other binders, before the statement's colon -- a binder after
    # the conclusion would not parse.
    return p.rebuild(binders=b.rstrip() + " (h_false : False)")


def _wrong_conclusion(p: _Parsed) -> Optional[str]:
    c = p.conclusion
    if " = " in c:
        new = c.replace(" = ", " ≠ ", 1)
    elif "≤" in c:
        new = c.replace("≤", ">", 1)
    elif " + " in c:
        new = c.replace(" + ", " - ", 1)
    elif _TRUE_RE.search(c):
        new = _TRUE_RE.sub("False", c, count=1)
    else:
        new = f"¬ ({c})"
    return p.rebuild(conclusion=new)


def generate_variants(theorem_statement: str) -> dict[str, Optional[str]]:
    """Return {"wrong_hypothesis": ..., "wrong_conclusion": ...}.

    Either value is None when the statement could not be parsed or the
    rewrite left it unchanged.
    """
    parsed = _parse(theorem_statement)
    out: dict[str, Optional[str]] = {k: None for k in VARIANT_KINDS}
    if parsed is None:
        return out
    for kind, fn in (("wrong_hypothesis", _wrong_hypothesis),
                     ("wrong_conclusion", _wrong_conclusion)):
        try:
            variant = fn(parsed)
        except Exception:
            variant = None
        if variant is not None and variant != theorem_statement:
            out[kind] = variant
    return out


# ---------------------------------------------------------------------------
# Experiment driver
# ---------------------------------------------------------------------------

def run_variant_experiment(theorem_statement: str, base_proof_id: str,
                           whole_proof_agent, logger: ProofLogger) -> dict:
    """Prove-attempt both variants of *theorem_statement* with *whole_proof_agent*.

    Each variant runs under `base_proof_id + "_wh"` / `"_wc"`, and a `variant`
    event ties that id back to the base proof and the original statement, so
    the log says which true theorem each false one came from. The agent's
    own start/step/done events follow as usual.

    A variant that could not be generated is reported with theorem None and
    result None. A circuit-breaker RuntimeError from the agent propagates.
    """
    variants = generate_variants(theorem_statement)
    report: dict = {"base_proof_id": base_proof_id}
    for kind in VARIANT_KINDS:
        variant = variants[kind]
        entry = {"theorem": variant, "result": None}
        if variant is not None:
            variant_id = base_proof_id + PROOF_ID_SUFFIX[kind]
            logger.log_variant(base_proof_id, kind, variant_id, theorem_statement, variant)
            entry["result"] = whole_proof_agent.attempt_proof(variant, variant_id)
        report[kind] = entry
    return report
