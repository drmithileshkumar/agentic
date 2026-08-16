#!/usr/bin/env python3
"""Extract candidate theorems from Lean sources into candidates.jsonl.

Stdlib only. No third-party dependencies.

Three modes:

  --seed              Re-read the verified problems/ tree into candidates.jsonl
                      (marks every entry verified: true). Harvested candidates
                      already in the manifest are carried over untouched.

  --sources <names>   Pull from Hugging Face; see DATASET_SOURCES below.
                      Needs `pip install datasets`.

  --source <dir>      Walk a directory of .lean files (a dataset checkout such
                      as PutnamBench / ProofNetSharp / miniF2F / FormalMATH),
                      pull out every `theorem` / `lemma` declaration, classify
                      it, and add whatever the manifest does not already hold.

All three MERGE into candidates.jsonl. The manifest is the only record of which
candidates have been promoted and verified, so nothing is dropped unless you
ask for it with --replace.

Every record is one JSON object per line:

  {"id", "name", "domain", "tier", "statement", "proof", "imports",
   "source_dataset", "source_file", "source_id", "informal", "verified",
   "problem_path", "notes"}

See problems/SETUP_COMPLETE.md for the full workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROBLEMS_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = PROBLEMS_DIR / "candidates.jsonl"

DOMAINS = [
    "SET", "TOP", "ALG", "ABA", "LIN", "NUM",
    "RAN", "CAN", "CMB", "GEO", "PRB",
]
TIERS = ["E", "M", "H"]
UNKNOWN_DOMAIN = "UNK"

# ---------------------------------------------------------------------------
# Domain classification
#
# Weighted keyword scoring over the Lean statement and the natural-language
# statement; highest total wins, ties break by the order of DOMAIN_KEYWORDS.
#
# Two rules earn their keep here:
#
#   * Identifiers match on token boundaries, not as substrings. `Basis` used to
#     fire inside `IsTopologicalBasis` and drag Munkres' topology exercises into
#     Linear Algebra. Unicode symbols (∩, ∣, ℝ) still match as substrings —
#     they have no word boundaries to speak of.
#   * The Lean statement outweighs the prose (see STATEMENT_WEIGHT). It names
#     Mathlib types, so it says what the problem *is*; the prose says what it is
#     about, which is softer evidence.
#
# Anything still scoring 0 falls back to the source prior (SOURCE_PRIORS) and
# only then to UNK.
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "PRB": [("ProbabilityTheory", 6), ("PMF", 5), ("MeasureTheory", 3),
            ("Measure", 3), ("indepFun", 5), ("IndepFun", 5), ("volume", 2),
            ("expectation", 4), ("probability", 4), ("random", 3)],
    "CAN": [("AnalyticAt", 6), ("AnalyticOn", 6), ("HolomorphicOn", 6),
            ("Complex", 5), ("ℂ", 5), ("holomorphic", 5), ("analytic", 4),
            ("meromorphic", 5), ("residue", 4), ("Complex.abs", 5),
            ("conformal", 4), ("DifferentiableOn", 2)],
    "GEO": [("EuclideanGeometry", 6), ("∠", 5), ("Collinear", 5),
            ("Triangle", 4), ("triangle", 4), ("Sphere", 4), ("Affine", 4),
            ("circle", 4), ("angle", 3), ("perpendicular", 4),
            ("hypotenuse", 5), ("quadrilateral", 5), ("polygon", 4),
            ("area of", 3), ("congruent", 2)],
    "LIN": [("Matrix", 6), ("LinearMap", 6), ("LinearIndependent", 6),
            ("Submodule", 5), ("FiniteDimensional", 5), ("eigenvalue", 6),
            ("eigenvector", 6), ("eigen", 4), ("determinant", 5),
            ("Basis", 4), ("Module", 4), ("span", 3), ("vector space", 6),
            ("linear transformation", 6), ("linearly independent", 6)],
    "TOP": [("TopologicalSpace", 6), ("IsTopologicalBasis", 6),
            ("LocallyCompactSpace", 6), ("Homeomorph", 6), ("CompactSpace", 5),
            ("ConnectedSpace", 5), ("IsConnected", 5), ("IsCompact", 5),
            ("IsOpen", 5), ("IsClosed", 5), ("T2Space", 5), ("nhds", 5),
            ("𝓝", 5), ("Continuous", 4), ("ContinuousOn", 4),
            ("topology", 5), ("topological", 5), ("homeomorphic", 5),
            ("open set", 4), ("closed set", 4), ("compact", 3),
            ("Hausdorff", 5), ("MetricSpace", 2), ("closure", 2),
            ("interior", 2)],
    "RAN": [("HasDerivAt", 6), ("intervalIntegral", 6), ("MeanValue", 5),
            ("Summable", 5), ("tsum", 5), ("deriv", 5), ("integral", 5),
            ("Tendsto", 5), ("atTop", 4), ("Cauchy", 4), ("converges", 4),
            ("differentiable", 4), ("uniformly", 4), ("supremum", 4),
            ("infimum", 4), ("series", 4), ("sequence", 3), ("iSup", 3),
            ("Real", 2), ("ℝ", 1)],
    "NUM": [("Nat.Prime", 6), ("Nat.Coprime", 6), ("ZMod", 6), ("padic", 6),
            ("totient", 6), ("Nat.factorial", 5), ("Nat.gcd", 5),
            ("divisors", 5), ("Irrational", 5), ("divisible", 5),
            ("divides", 5), ("modulo", 5), ("pmod", 5), ("remainder", 4),
            ("congruent to", 5), ("gcd", 4), ("prime", 4), ("∣", 4),
            ("%", 2), ("Int", 2)],
    "CMB": [("number of ways", 6), ("SimpleGraph", 6), ("Nat.choose", 6),
            ("Finset.card", 5), ("Equiv.Perm", 5), ("Fintype.card", 4),
            ("how many", 4), ("permutation", 4), ("binomial", 4),
            ("choose", 3), ("combinatori", 5), ("Finset.sum", 1)],
    "ABA": [("QuotientGroup", 6), ("IsCyclic", 6), ("Sylow", 6),
            ("Subgroup", 6), ("Ideal", 6), ("MonoidHom", 5), ("RingHom", 5),
            ("orderOf", 5), ("→*", 5), ("normal subgroup", 6),
            ("abelian", 5), ("subgroup", 5), ("coset", 5), ("ideal", 5),
            ("CommGroup", 4), ("CommRing", 4), ("Group", 3), ("Ring", 3),
            ("homomorphism", 3), ("isomorphic", 2)],
    "SET": [("Set.univ", 5), ("Function.Injective", 4),
            ("Function.Surjective", 4), ("Function.Bijective", 4),
            ("Countable", 4), ("∩", 5), ("∪", 5), ("⊆", 5), ("⋃", 5),
            ("⋂", 5), ("cardinality", 4), ("countable", 4),
            ("bijection", 4), ("surjective", 3), ("injective", 3),
            ("Set", 3), ("compl", 2)],
    "ALG": [("Polynomial", 5), ("inequality", 4), ("logarithm", 4),
            ("field_simp", 3), ("nlinarith", 2), ("linarith", 2),
            ("equation", 3), ("simplify", 3), ("evaluate", 2),
            ("expression", 2), ("value of", 2), ("Field", 2), ("Monoid", 2),
            # A bare product of numerals is an identity to be manipulated. Low
            # weight: this only decides entries nothing else has an opinion on.
            ("∏", 2)],
}

# The Lean statement is precise and the prose is only suggestive, so prose hits
# score at a discount rather than on equal footing.
STATEMENT_WEIGHT = 1.0
INFORMAL_WEIGHT = 0.5

# Where a problem came from is real evidence about its subject. Munkres is a
# topology book; Axler is a linear algebra book. This prior is a tiebreaker
# nudge when keywords already agree, and the fallback when they say nothing at
# all — which is what used to leave 8 ProofNet entries in UNK.
SOURCE_PRIORS: dict[str, str] = {
    "munkres": "TOP",
    "rudin": "RAN",
    "pugh": "RAN",
    "shakarchi": "CAN",
    "axler": "LIN",
    "artin": "ABA",
    "herstein": "ABA",
    "dummit-foote": "ABA",
    "dummit": "ABA",
    "ireland-rosen": "NUM",
    "ireland": "NUM",
}
PRIOR_BONUS = 2.5

# Absolute last resort, applied only when nothing else matched: the number type
# a statement is phrased over. Ordered, first hit wins.
TYPE_FALLBACKS = (
    ("ℂ", "CAN"),
    ("ℤ", "NUM"),
    ("ℕ", "NUM"),
    ("ℚ", "ALG"),
    ("ℝ", "ALG"),
)

# Datasets whose problems are competition-hard by construction. Used by the
# tier heuristic when the source path hints at a known dataset.
HARD_DATASET_HINTS = ("putnam", "imo", "olympiad")
MEDIUM_DATASET_HINTS = ("proofnet", "formalmath")

# ---------------------------------------------------------------------------
# Hugging Face dataset sources (--sources)
#
# Each entry says which HF dataset to pull and which columns hold the Lean
# statement, the preamble, and the natural-language statement. Requires the
# `datasets` package; nothing here is vendored into the repo.
#
# To add a source: check its column names first, then add an entry. Putnam and
# FormalMATH are deliberately absent — add them once their columns are checked.
# ---------------------------------------------------------------------------

DATASET_SOURCES: dict[str, dict] = {
    "proofnet": {
        "hf_id": "PAug/ProofNetSharp",
        "label": "ProofNetSharp",
        "statement_field": "lean4_formalization",
        "header_field": "lean4_src_header",
        "informal_field": "nl_statement",
        "id_field": "id",
        # No blanket tier. ProofNet used to be mapped wholesale to M, which
        # meant 371 of 837 entries carried no difficulty signal at all; they
        # are scored like everything else now.
        "default_tier": None,
    },
    "minif2f": {
        "hf_id": "cat-searcher/minif2f-lean4",
        "label": "miniF2F",
        "statement_field": "formal_statement",
        "header_field": "header",
        "informal_field": "informal_stmt",
        "id_field": "id",
        "default_tier": None,         # per-problem, see guess_tier
    },
}

# miniF2F ids sometimes name their topic (`mathd_algebra_*`, `numbertheory_*`).
# Only the topic words are hints. The competition names are deliberately absent:
# mapping every `imo_*` to ALG put 39 olympiad problems in Algebra, number
# theory and geometry included, which is a statement about the filename rather
# than the problem. Those are classified from their content like everything else.
MINIF2F_DOMAIN_HINTS = (
    ("numbertheory", "NUM"),
    ("number_theory", "NUM"),
    ("algebra", "ALG"),
)

DECL_RE = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?"                      # optional attribute
    r"(?:private\s+|protected\s+|nonrec\s+)*"    # optional modifiers
    r"(theorem|lemma)\s+"
    r"([^\s:({\[]+)",                            # declaration name
    re.MULTILINE,
)

BLOCK_COMMENT_RE = re.compile(r"/-.*?-/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")
IMPORT_RE = re.compile(r"^import\s+(\S+)", re.MULTILINE)

# A new top-level declaration ends the previous one.
NEXT_DECL_RE = re.compile(
    r"^(?:@\[|theorem\s|lemma\s|def\s|abbrev\s|example\s|instance\s|"
    r"structure\s|inductive\s|class\s|namespace\s|end\s|section\s|"
    r"open\s|import\s|variable\s|noncomputable\s)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_comments(text: str) -> str:
    """Remove Lean block and line comments.

    Naive: a `--` inside a string literal would also be stripped. Math
    statements essentially never contain one, so this is good enough.
    """
    text = BLOCK_COMMENT_RE.sub("", text)
    return LINE_COMMENT_RE.sub("", text)


def normalize(statement: str) -> str:
    """Collapse whitespace so near-identical statements hash alike."""
    return re.sub(r"\s+", " ", statement).strip()


def statement_key(statement: str) -> str:
    """Identity of a statement, ignoring its declaration name.

    Datasets name the same theorem differently, so the name must not take part
    in dedup or id generation.
    """
    return re.sub(r"^(?:theorem|lemma)\s+\S+\s*", "", normalize(statement))


def make_id(statement: str) -> str:
    return hashlib.sha1(statement_key(statement).encode("utf-8")).hexdigest()[:12]


def split_at_assign(body: str) -> tuple[str, str]:
    """Split a declaration into (statement, proof) at the top-level `:=`.

    Depth-aware so `:=` inside (), {}, [] or ⟨⟩ does not fool us. Returns
    (body, "") when there is no top-level `:=`.
    """
    depth = 0
    openers, closers = "({[⟨", ")}]⟩"
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in openers:
            depth += 1
        elif ch in closers:
            depth -= 1
        elif ch == ":" and depth == 0 and body[i + 1:i + 2] == "=":
            return body[:i].rstrip(), body[i + 2:].strip()
        i += 1
    return body.rstrip(), ""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.' ]*$")


def _compile_keyword(keyword: str) -> re.Pattern[str]:
    """Token-boundary matcher for identifiers, plain substring for symbols.

    `Basis` must not match inside `IsTopologicalBasis`, but `∩` has no word
    boundary to anchor against, so symbols stay substring matches.
    """
    if _IDENTIFIER_RE.match(keyword):
        # `.` is deliberately absent from the lookbehind: `divisors` has to keep
        # matching inside `Nat.divisors`. The lookahead is what stops `Basis`
        # from firing inside `IsTopologicalBasis`.
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(keyword)
                          + r"(?![A-Za-z0-9_])", re.IGNORECASE)
    return re.compile(re.escape(keyword))


KEYWORD_PATTERNS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    domain: [(_compile_keyword(kw), weight) for kw, weight in keywords]
    for domain, keywords in DOMAIN_KEYWORDS.items()
}


def source_prior(source_id: str) -> str | None:
    """The domain a problem's textbook or collection suggests, if any."""
    lowered = (source_id or "").lower()
    for needle, domain in SOURCE_PRIORS.items():
        if needle in lowered:
            return domain
    return None


def guess_domain(statement: str, informal: str = "",
                 source_id: str = "") -> tuple[str, float]:
    """Return (domain, score). Score 0 means 'no idea' -> UNK.

    Scores the Lean statement and the prose separately so the statement can
    outweigh the prose, then nudges by the source prior.
    """
    prior = source_prior(source_id)
    scores: dict[str, float] = {}
    for domain, patterns in KEYWORD_PATTERNS.items():
        score = 0.0
        for pattern, weight in patterns:
            if pattern.search(statement):
                score += weight * STATEMENT_WEIGHT
            if informal and pattern.search(informal):
                score += weight * INFORMAL_WEIGHT
        if domain == prior:
            # Unconditional: a Herstein exercise on quaternions scores nothing
            # for Abstract Algebra by keyword and a stray point for Real
            # Analysis off the `ℝ`. The book it came from has to be able to win
            # that from zero.
            score += PRIOR_BONUS
        scores[domain] = score

    # Ties break by DOMAIN_KEYWORDS order, which max() preserves.
    best = max(scores, key=lambda d: scores[d])
    if scores[best] > 0:
        return best, scores[best]

    # Last resort. Bare arithmetic over a number type — most of miniF2F's AMC
    # and AIME entries — matches no keyword at all, but the ambient type is
    # still evidence, and a guess we can name beats a UNK bucket nobody triages.
    for needle, domain in TYPE_FALLBACKS:
        if needle in statement:
            return domain, 0.5
    return UNKNOWN_DOMAIN, 0.0


def count_tactics(proof: str) -> int:
    """Rough tactic count: `;`- and newline-separated steps inside the proof."""
    proof = proof.strip()
    if not proof:
        return 0
    steps = [s for s in re.split(r"[;\n]", proof.replace("by", "", 1)) if s.strip()]
    return len(steps)


HYPOTHESIS_RE = re.compile(r"\([^():]*:[^()]*\)")
QUANTIFIER_RE = re.compile(r"[∀∃]")
CONNECTIVE_RE = re.compile(r"[↔→∧∨]")


def statement_complexity(statement: str) -> int:
    """A structural difficulty score for a Lean statement.

    Counts the things that actually make a goal harder to discharge: how much
    context you are handed, how deeply quantified the goal is, and how long it
    is. This is a proxy, not a judgement about the mathematics — but it is a
    proxy computed from the problem rather than from the filename, which is the
    point. See TIER_THRESHOLDS for how the score maps onto E/M/H.
    """
    body = normalize(statement)
    score = 0
    score += 2 * len(HYPOTHESIS_RE.findall(body))
    score += 2 * len(QUANTIFIER_RE.findall(body))
    score += len(CONNECTIVE_RE.findall(body))
    score += len(body) // 60
    return score


# Complexity below the first number is Easy, below the second is Medium, at or
# above it is Hard. Tuned so the ProofNet corpus lands roughly 25/55/20.
TIER_THRESHOLDS = (6, 16)

# Some collections are hard by construction and no structural score should be
# allowed to talk us out of it. A short IMO statement is still an IMO problem.
COMPETITION_FLOORS = (
    ("putnam", "H"),
    ("imo", "H"),
    ("aime", "H"),
    ("usamo", "H"),
    ("olympiad", "H"),
    ("amc", "M"),
)


def guess_tier(statement: str, proof: str, source_path: str) -> str:
    """E = single concept, M = undergraduate multi-step, H = competition.

    Order matters: a competition floor wins outright, then a short proof we can
    actually measure, then the structural complexity of the statement.
    """
    floor = hint_lookup(source_path, COMPETITION_FLOORS)
    if floor == "H":
        return "H"

    # A proof we were handed is the best evidence available: if the source
    # closed it in a few tactics, it is easy regardless of how it reads.
    tactics = count_tactics(proof)
    if proof and tactics and tactics <= 3 and len(normalize(statement)) <= 160:
        return "E"

    easy_max, hard_min = TIER_THRESHOLDS
    complexity = statement_complexity(statement)
    if complexity >= hard_min:
        tier = "H"
    elif complexity <= easy_max:
        tier = "E"
    else:
        tier = "M"

    # An AMC problem is at least Medium even when it looks structurally simple.
    if floor == "M" and tier == "E":
        return "M"
    return tier


def guess_dataset(path: Path, source_root: Path) -> str:
    """Name the dataset after the first path component under the source root."""
    try:
        rel = path.relative_to(source_root)
    except ValueError:
        return source_root.name
    return rel.parts[0] if len(rel.parts) > 1 else source_root.name


def iter_declarations(path: Path):
    """Yield (name, statement, proof, imports) for each theorem/lemma in a file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  ! skipped {path}: {exc}", file=sys.stderr)
        return

    imports = IMPORT_RE.findall(raw)
    text = strip_comments(raw)
    lines = text.splitlines()

    starts = []
    for idx, line in enumerate(lines):
        match = DECL_RE.match(line)
        if match:
            starts.append((idx, match.group(2)))

    for pos, (start, name) in enumerate(starts):
        # The declaration runs until the next top-level declaration keyword.
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if NEXT_DECL_RE.match(lines[idx]):
                end = idx
                break
        if pos + 1 < len(starts):
            end = min(end, starts[pos + 1][0])

        block = "\n".join(lines[start:end]).strip()
        statement, proof = split_at_assign(block)
        if not statement:
            continue
        yield name, statement, proof, imports


def existing_statements(problems_dir: Path) -> set[str]:
    """Name-independent keys for everything already in the problems tree."""
    seen = set()
    for lean_file in sorted(problems_dir.rglob("*.lean")):
        for _name, statement, _proof, _imports in iter_declarations(lean_file):
            seen.add(statement_key(statement))
    return seen


def solved_statements(existing: list[dict]) -> set[str]:
    """Every statement we already hold, from the problems/ tree and the manifest.

    Used to drop dataset rows that restate something the benchmark already has.
    Reading the manifest matters as much as reading the tree: merging is the
    default, so the manifest is the record of what we hold, and a candidate must
    not come back under a fresh id just because a dataset spells it differently.
    """
    seen = existing_statements(PROBLEMS_DIR)
    for record in existing:
        statement = record.get("statement")
        if statement:
            seen.add(statement_key(statement))
    return seen


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"  ! {path.name}:{lineno} is not valid JSON: {exc}", file=sys.stderr)
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_record(
    *,
    name: str,
    statement: str,
    proof: str,
    imports: list[str],
    dataset: str,
    source_file: str,
    domain: str,
    tier: str,
    verified: bool,
    problem_path: str = "",
    notes: str = "",
    informal: str = "",
    source_id: str = "",
) -> dict:
    return {
        "id": make_id(statement),
        "name": name,
        "domain": domain,
        "tier": tier,
        "statement": statement,
        "proof": proof,
        "imports": imports or ["Mathlib"],
        "source_dataset": dataset,
        "source_file": source_file,
        "source_id": source_id,
        "informal": informal,
        "verified": verified,
        "problem_path": problem_path,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_seed(out_path: Path, replace: bool) -> int:
    """Refresh the seed entries in candidates.jsonl from the problems/ tree.

    Seed entries are re-read from disk; everything harvested from a dataset is
    carried over untouched. `--replace` throws the harvested candidates away.
    """
    records = []
    for lean_file in sorted(PROBLEMS_DIR.rglob("*.lean")):
        rel = lean_file.relative_to(PROBLEMS_DIR).as_posix()
        parts = lean_file.relative_to(PROBLEMS_DIR).parts
        domain = parts[0] if len(parts) >= 3 and parts[0] in DOMAINS else UNKNOWN_DOMAIN
        tier = parts[1] if len(parts) >= 3 and parts[1] in TIERS else "E"
        for name, statement, proof, imports in iter_declarations(lean_file):
            records.append(make_record(
                name=name,
                statement=statement,
                proof=proof,
                imports=imports,
                dataset="seed",
                source_file=f"problems/{rel}",
                domain=domain,
                tier=tier,
                verified=True,
                problem_path=f"problems/{rel}",
                notes="hand-verified seed problem",
            ))

    seeded_ids = {r["id"] for r in records}
    carried = []
    if not replace:
        carried = [r for r in load_jsonl(out_path)
                   if r.get("source_dataset") != "seed" and r.get("id") not in seeded_ids]

    write_jsonl(out_path, records + carried)
    print(f"Seeded {len(records)} verified entries -> {out_path}")
    if carried:
        print(f"Carried over {len(carried)} harvested candidates "
              f"({len(records) + len(carried)} total)")
    elif not replace:
        print("No harvested candidates to carry over")
    return 0


def run_extract(args: argparse.Namespace, out_path: Path) -> int:
    source_root = Path(args.source).resolve()
    if not source_root.is_dir():
        print(f"error: --source {source_root} is not a directory", file=sys.stderr)
        return 1

    existing = [] if args.replace else load_jsonl(out_path)
    known_ids = {r.get("id") for r in existing}
    already_solved = solved_statements(existing)

    new_records, skipped_dupe, skipped_filter = [], 0, 0
    for lean_file in sorted(source_root.rglob("*.lean")):
        rel = lean_file.relative_to(source_root).as_posix()
        dataset = guess_dataset(lean_file, source_root)
        for name, statement, proof, imports in iter_declarations(lean_file):
            if statement_key(statement) in already_solved:
                skipped_dupe += 1
                continue

            domain, tier, score = classify(
                statement=statement,
                proof=proof,
                informal="",
                source_id=rel,
                source_label=dataset,
                forced_domain=args.domain,
                forced_tier=args.tier,
            )

            if args.only_domain and domain != args.only_domain:
                skipped_filter += 1
                continue
            if args.only_tier and tier != args.only_tier:
                skipped_filter += 1
                continue

            notes = "" if score else "domain unclassified - set it before promoting"
            record = make_record(
                name=name,
                statement=statement,
                proof="" if args.strip_proofs else proof,
                imports=imports,
                dataset=dataset,
                source_file=rel,
                domain=domain,
                tier=tier,
                verified=False,
                notes=notes,
            )
            if record["id"] in known_ids:
                skipped_dupe += 1
                continue
            known_ids.add(record["id"])
            new_records.append(record)
            if args.limit and len(new_records) >= args.limit:
                break
        if args.limit and len(new_records) >= args.limit:
            break

    print(f"Scanned  : {source_root}")
    print(f"New      : {len(new_records)}")
    print(f"Duplicate: {skipped_dupe}")
    if args.only_domain or args.only_tier:
        print(f"Filtered : {skipped_filter}")

    if args.dry_run:
        for record in new_records[:10]:
            print(f"  {record['id']}  {record['domain']}/{record['tier']}  {record['name']}")
        if len(new_records) > 10:
            print(f"  ... and {len(new_records) - 10} more")
        print("(dry run - nothing written)")
        return 0

    write_jsonl(out_path, existing + new_records)
    print(f"Wrote    : {out_path} ({len(existing) + len(new_records)} total)")
    return 0


def run_reclassify(args, out_path: Path) -> int:
    """Re-run domain/tier classification over the manifest, in place.

    Verified entries keep the domain and tier they were promoted under: those
    are hand-assigned and they name a directory on disk, so moving them would
    orphan the .lean file. Everything else is reclassified.
    """
    records = load_jsonl(out_path)
    if not records:
        print(f"error: {out_path} is empty or missing", file=sys.stderr)
        return 1

    domain_moves: dict[tuple[str, str], int] = {}
    tier_moves: dict[tuple[str, str], int] = {}
    changed = 0

    for record in records:
        if record.get("verified"):
            continue
        source_id = record.get("source_id") or record.get("source_file") or ""
        label = record.get("source_dataset") or ""
        hints = MINIF2F_DOMAIN_HINTS if label == "miniF2F" else ()
        domain, tier, score = classify(
            statement=record.get("statement") or "",
            proof=record.get("proof") or "",
            informal=record.get("informal") or "",
            source_id=source_id,
            source_label=label,
            forced_domain=args.domain,
            forced_tier=args.tier,
            topic_hints=hints,
        )
        old_domain, old_tier = record.get("domain"), record.get("tier")
        if domain != old_domain:
            domain_moves[(old_domain, domain)] = domain_moves.get((old_domain, domain), 0) + 1
        if tier != old_tier:
            tier_moves[(old_tier, tier)] = tier_moves.get((old_tier, tier), 0) + 1
        if domain != old_domain or tier != old_tier:
            changed += 1
        record["domain"] = domain
        record["tier"] = tier
        record["notes"] = ("" if score else
                           "domain unclassified - set it before promoting")

    print(f"Reclassified {changed} of {len(records)} entries")
    if domain_moves:
        print("\ndomain changes:")
        for (old, new), count in sorted(domain_moves.items(), key=lambda kv: -kv[1]):
            print(f"  {old or '-':<4} -> {new:<4} {count}")
    if tier_moves:
        print("\ntier changes:")
        for (old, new), count in sorted(tier_moves.items(), key=lambda kv: -kv[1]):
            print(f"  {old or '-':<4} -> {new:<4} {count}")

    if args.dry_run:
        print("\n(dry run - nothing written)")
        return 0
    write_jsonl(out_path, records)
    print(f"\nWrote {out_path}")
    return 0


def parse_lean_statement(text: str) -> tuple[str, str, str]:
    """Parse one dataset row's Lean source into (name, statement, proof)."""
    cleaned = strip_comments(text or "").strip()
    match = DECL_RE.search(cleaned)
    if not match:
        return "", "", ""
    block = cleaned[match.start():].strip()
    statement, proof = split_at_assign(block)
    # A `sorry` placeholder is the absence of a proof, not a proof.
    if re.fullmatch(r"(by\s+)?sorry", proof.strip()):
        proof = ""
    return match.group(2), statement, proof


def hint_lookup(text: str, hints: tuple[tuple[str, str], ...]) -> str | None:
    lowered = (text or "").lower()
    for needle, value in hints:
        if needle in lowered:
            return value
    return None


def classify(statement: str, proof: str, informal: str, source_id: str,
             source_label: str, forced_domain: str | None = None,
             forced_tier: str | None = None,
             topic_hints: tuple[tuple[str, str], ...] = ()) -> tuple[str, str, float]:
    """Assign (domain, tier, domain_score) to one problem.

    The single place classification happens, so `--source`, `--sources` and
    `--reclassify` cannot drift apart.
    """
    domain = forced_domain or hint_lookup(source_id, topic_hints)
    score = float(PRIOR_BONUS) if domain else 0.0
    if not domain:
        domain, score = guess_domain(statement, informal, source_id)

    tier = forced_tier
    if not tier:
        # Both the id and the dataset name can carry a competition hint.
        tier = guess_tier(statement, proof, f"{source_label} {source_id}")
    return domain, tier, score


def run_datasets(args, out_path: Path) -> int:
    """Pull candidates from the Hugging Face datasets named in --sources."""
    try:
        from datasets import load_dataset  # noqa: PLC0415
    except ImportError:
        print("error: the `datasets` package is required for --sources.\n"
              "       pip install datasets", file=sys.stderr)
        return 1

    wanted = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in DATASET_SOURCES]
    if unknown:
        print(f"error: unknown source(s): {', '.join(unknown)}\n"
              f"       known: {', '.join(DATASET_SOURCES)}", file=sys.stderr)
        return 1

    existing = [] if args.replace else load_jsonl(out_path)
    known_ids = {r.get("id") for r in existing}
    already_solved = solved_statements(existing)

    new_records: list[dict] = []
    per_source: dict[str, int] = {}
    skipped_dupe = skipped_unparsed = 0

    for source in wanted:
        spec = DATASET_SOURCES[source]
        print(f"Pulling {spec['hf_id']} ...")
        try:
            dataset = load_dataset(spec["hf_id"])
        except Exception as exc:  # network, auth, renamed repo, ...
            print(f"  ! could not load {spec['hf_id']}: {exc}", file=sys.stderr)
            continue

        count = 0
        for split_name, split in dataset.items():
            for row in split:
                raw = row.get(spec["statement_field"]) or ""
                decl_name, statement, proof = parse_lean_statement(raw)
                if not statement:
                    skipped_unparsed += 1
                    continue

                if statement_key(statement) in already_solved:
                    skipped_dupe += 1
                    continue

                source_id = str(row.get(spec["id_field"], "") or "")
                informal = (row.get(spec["informal_field"]) or "").strip()
                header = row.get(spec["header_field"]) or ""
                imports = IMPORT_RE.findall(header) or ["Mathlib"]

                domain, tier, _score = classify(
                    statement=statement,
                    proof=proof,
                    informal=informal,
                    source_id=source_id,
                    source_label=spec["label"],
                    forced_domain=args.domain,
                    forced_tier=args.tier or spec.get("default_tier"),
                    topic_hints=(MINIF2F_DOMAIN_HINTS if source == "minif2f"
                                 else ()),
                )

                if args.only_domain and domain != args.only_domain:
                    continue
                if args.only_tier and tier != args.only_tier:
                    continue

                record = make_record(
                    name=decl_name,
                    statement=statement,
                    proof="" if args.strip_proofs else proof,
                    imports=imports,
                    dataset=spec["label"],
                    source_file=f"{spec['hf_id']}#{split_name}",
                    source_id=source_id,
                    informal=informal,
                    domain=domain,
                    tier=tier,
                    verified=False,
                    notes="" if domain != UNKNOWN_DOMAIN
                          else "domain unclassified - set it before promoting",
                )
                if record["id"] in known_ids:
                    skipped_dupe += 1
                    continue
                known_ids.add(record["id"])
                new_records.append(record)
                count += 1
                if args.limit and len(new_records) >= args.limit:
                    break
            if args.limit and len(new_records) >= args.limit:
                break

        per_source[spec["label"]] = count
        print(f"  {spec['label']}: {count} new")
        if args.limit and len(new_records) >= args.limit:
            print(f"  (stopped at --limit {args.limit})")
            break

    print()
    for label, count in per_source.items():
        print(f"{label:<16} {count}")
    print(f"{'New total':<16} {len(new_records)}")
    print(f"{'Duplicates':<16} {skipped_dupe}")
    if skipped_unparsed:
        print(f"{'Unparsed rows':<16} {skipped_unparsed}")

    if args.dry_run:
        print("(dry run - nothing written)")
        return 0

    write_jsonl(out_path, existing + new_records)
    print(f"\nWrote {out_path} ({len(existing) + len(new_records)} entries total)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract Lean theorem candidates into candidates.jsonl.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python problems/extract_candidates.py --seed\n"
            "  python problems/extract_candidates.py --sources proofnet,minif2f\n"
            "  python problems/extract_candidates.py --source ../datasets/ProofNetSharp\n"
            "  python problems/extract_candidates.py --source ../datasets --limit 50\n"
            "  python problems/extract_candidates.py --source ../datasets --only-domain TOP --dry-run\n"
            "\n"
            "Every mode MERGES into candidates.jsonl. Nothing is ever dropped unless\n"
            "you pass --replace, which discards the whole manifest first.\n"
        ),
    )
    parser.add_argument("--source", help="directory of .lean files to scan")
    parser.add_argument("--sources",
                        help="comma-separated Hugging Face sources to pull: "
                             + ", ".join(DATASET_SOURCES) + " (needs `pip install datasets`)")
    parser.add_argument("--seed", action="store_true",
                        help="rebuild candidates.jsonl from the verified problems/ tree")
    parser.add_argument("--reclassify", action="store_true",
                        help="re-run domain/tier classification over the existing "
                             "manifest in place (verified entries are left alone)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSONL (default: problems/candidates.jsonl)")
    parser.add_argument("--replace", action="store_true",
                        help="DESTRUCTIVE: discard the existing manifest instead of "
                             "merging into it. You lose every verified/promoted entry.")
    parser.add_argument("--append", action="store_true",
                        help=argparse.SUPPRESS)  # accepted for compatibility; now the default
    parser.add_argument("--domain", choices=DOMAINS, help="force this domain on every extracted candidate")
    parser.add_argument("--tier", choices=TIERS, help="force this tier on every extracted candidate")
    parser.add_argument("--only-domain", choices=DOMAINS, help="keep only candidates classified into this domain")
    parser.add_argument("--only-tier", choices=TIERS, help="keep only candidates in this tier")
    parser.add_argument("--limit", type=int, help="stop after N new candidates")
    parser.add_argument("--strip-proofs", action="store_true",
                        help="discard source proofs (keep statements only)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    args = parser.parse_args(argv)

    out_path = Path(args.out).resolve()

    if args.reclassify:
        return run_reclassify(args, out_path)
    if args.seed:
        return run_seed(out_path, args.replace)
    if args.sources:
        return run_datasets(args, out_path)
    if not args.source:
        parser.error("one of --seed, --sources, --source or --reclassify is required")
    return run_extract(args, out_path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
