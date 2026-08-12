#!/usr/bin/env python3
"""Extract candidate theorems from Lean sources into candidates.jsonl.

Stdlib only. No third-party dependencies.

Two modes:

  --seed              Bootstrap candidates.jsonl from the already-verified
                      problems/ tree (marks every entry verified: true).

  --source <dir>      Walk a directory of .lean files (a dataset checkout such
                      as PutnamBench / ProofNetSharp / miniF2F / FormalMATH),
                      pull out every `theorem` / `lemma` declaration, guess a
                      domain + difficulty tier, drop anything that duplicates a
                      problem already in problems/, and append the rest to
                      candidates.jsonl as unverified candidates.

Every record is one JSON object per line:

  {"id", "name", "domain", "tier", "statement", "proof", "imports",
   "source_dataset", "source_file", "verified", "problem_path", "notes"}

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
# Ordered keyword scoring: each hit is worth its weight, highest total wins.
# Ties break by the order of DOMAIN_KEYWORDS below. Anything scoring 0 is
# tagged UNK and must be given a domain by hand before it can be promoted.
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "PRB": [("ProbabilityTheory", 4), ("MeasureTheory", 3), ("Measure ", 2),
            ("volume", 2), ("PMF", 3), ("expectation", 3), ("indepFun", 3)],
    "CAN": [("Complex", 3), ("ℂ", 3), ("AnalyticAt", 4), ("AnalyticOn", 4),
            ("DifferentiableOn", 2), ("Holomorphic", 4)],
    "GEO": [("EuclideanSpace", 4), ("EuclideanGeometry", 4), ("∠", 3),
            ("Triangle", 3), ("Sphere", 2), ("Collinear", 3), ("Affine", 2)],
    "LIN": [("Matrix", 4), ("LinearMap", 4), ("Basis", 3), ("det ", 3),
            ("eigen", 3), ("Module", 2), ("span", 2), ("Submodule", 3)],
    "TOP": [("TopologicalSpace", 4), ("IsOpen", 3), ("IsClosed", 3),
            ("Continuous", 3), ("IsCompact", 3), ("nhds", 3), ("𝓝", 3),
            ("Homeomorph", 4), ("MetricSpace", 2)],
    "RAN": [("Real", 3), ("ℝ", 2), ("deriv", 3), ("HasDerivAt", 4),
            ("integral", 3), ("Tendsto", 3), ("atTop", 2), ("iSup", 2),
            ("Summable", 3), ("tsum", 3), ("MeanValue", 3)],
    "NUM": [("Nat.Prime", 4), ("Nat.gcd", 3), ("Nat.Coprime", 4), ("ZMod", 4),
            ("∣", 2), ("Int.", 2), ("Nat.factorial", 3), ("divisors", 3),
            ("padic", 4), ("totient", 4)],
    "CMB": [("Finset.card", 4), ("Fintype.card", 3), ("choose", 3),
            ("Finset.sum", 2), ("Nat.choose", 4), ("SimpleGraph", 4),
            ("Equiv.Perm", 3)],
    "ABA": [("Subgroup", 4), ("Ideal", 4), ("QuotientGroup", 4), ("→*", 3),
            ("MonoidHom", 3), ("RingHom", 3), ("IsCyclic", 4),
            ("Sylow", 4), ("Normal", 2), ("Group ", 2), ("CommRing", 2)],
    "SET": [("Set ", 3), ("Set.", 3), ("∩", 3), ("∪", 3), ("⊆", 3),
            ("Set.univ", 3), ("compl", 2), ("⋃", 3), ("⋂", 3)],
    "ALG": [("ring", 2), ("Polynomial", 3), ("field_simp", 2), ("^2", 1),
            ("Field", 2), ("Monoid", 2), ("mul_inv", 2), ("linarith", 1),
            ("nlinarith", 1)],
}

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
        "default_tier": "M",          # README maps all of ProofNet to tier M
    },
    "minif2f": {
        "hf_id": "cat-searcher/minif2f-lean4",
        "label": "miniF2F",
        "statement_field": "formal_statement",
        "header_field": "header",
        "informal_field": "informal_stmt",
        "id_field": "id",
        "default_tier": None,         # per-problem, see MINIF2F_TIER_HINTS
    },
}

# miniF2F bundles several competitions at very different difficulties, so its
# tier comes from the problem id rather than one blanket mapping.
MINIF2F_TIER_HINTS = (
    ("imo", "H"),
    ("aime", "H"),
    ("amc", "M"),
    ("mathd", "E"),
    ("induction", "E"),
    ("algebra", "E"),
    ("numbertheory", "E"),
)

# miniF2F ids carry their topic (mathd_algebra_*, numbertheory_*), which beats
# keyword scoring on the Lean source.
MINIF2F_DOMAIN_HINTS = (
    ("numbertheory", "NUM"),
    ("number_theory", "NUM"),
    ("algebra", "ALG"),
    ("induction", "NUM"),
    ("amc", "ALG"),
    ("aime", "ALG"),
    ("imo", "ALG"),
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


def guess_domain(text: str) -> tuple[str, int]:
    """Return (domain, score). Score 0 means 'no idea' -> UNK."""
    best, best_score = UNKNOWN_DOMAIN, 0
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(weight for kw, weight in keywords if kw in text)
        if score > best_score:
            best, best_score = domain, score
    return best, best_score


def count_tactics(proof: str) -> int:
    """Rough tactic count: `;`- and newline-separated steps inside the proof."""
    proof = proof.strip()
    if not proof:
        return 0
    steps = [s for s in re.split(r"[;\n]", proof.replace("by", "", 1)) if s.strip()]
    return len(steps)


def guess_tier(statement: str, proof: str, source_path: str) -> str:
    """E = 1-4 tactics / single concept, H = competition, M = everything else."""
    # A known dataset is a stronger signal than proof length: the README maps
    # PutnamBench -> H and ProofNetSharp/FormalMATH -> M wholesale.
    lowered = source_path.lower()
    if any(hint in lowered for hint in HARD_DATASET_HINTS):
        return "H"
    if any(hint in lowered for hint in MEDIUM_DATASET_HINTS):
        return "M"
    tactics = count_tactics(proof)
    if proof and tactics and tactics <= 4 and len(normalize(statement)) <= 200:
        return "E"
    if not proof:
        # No proof to measure; judge on statement size alone.
        return "E" if len(normalize(statement)) <= 120 else "M"
    return "M"


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

def run_seed(out_path: Path) -> int:
    """Rebuild candidates.jsonl from the verified problems/ tree."""
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

    write_jsonl(out_path, records)
    print(f"Seeded {len(records)} verified entries -> {out_path}")
    return 0


def run_extract(args: argparse.Namespace, out_path: Path) -> int:
    source_root = Path(args.source).resolve()
    if not source_root.is_dir():
        print(f"error: --source {source_root} is not a directory", file=sys.stderr)
        return 1

    existing = load_jsonl(out_path) if args.append else []
    known_ids = {r.get("id") for r in existing}
    already_solved = existing_statements(PROBLEMS_DIR)

    new_records, skipped_dupe, skipped_filter = [], 0, 0
    for lean_file in sorted(source_root.rglob("*.lean")):
        rel = lean_file.relative_to(source_root).as_posix()
        dataset = guess_dataset(lean_file, source_root)
        for name, statement, proof, imports in iter_declarations(lean_file):
            if statement_key(statement) in already_solved:
                skipped_dupe += 1
                continue

            haystack = f"{statement}\n{proof}"
            domain, score = guess_domain(haystack)
            if args.domain:
                domain, score = args.domain, max(score, 1)
            tier = args.tier or guess_tier(statement, proof, f"{dataset}/{rel}")

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

    existing = load_jsonl(out_path) if args.append else []
    known_ids = {r.get("id") for r in existing}
    already_solved = existing_statements(PROBLEMS_DIR)

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

                # Domain: id hints first for miniF2F, else keyword scoring.
                domain = None
                if source == "minif2f":
                    domain = hint_lookup(source_id, MINIF2F_DOMAIN_HINTS)
                if args.domain:
                    domain = args.domain
                if not domain:
                    domain, score = guess_domain(f"{statement}\n{informal}")
                    if not score:
                        domain = UNKNOWN_DOMAIN

                # Tier: explicit override, then per-source rule, then heuristic.
                tier = args.tier or spec.get("default_tier")
                if not tier and source == "minif2f":
                    tier = hint_lookup(source_id, MINIF2F_TIER_HINTS)
                if not tier:
                    tier = guess_tier(statement, proof, spec["label"])

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
            "  python problems/extract_candidates.py --source ../datasets/ProofNetSharp\n"
            "  python problems/extract_candidates.py --source ../datasets --append --limit 50\n"
            "  python problems/extract_candidates.py --source ../datasets --only-domain TOP --dry-run\n"
        ),
    )
    parser.add_argument("--source", help="directory of .lean files to scan")
    parser.add_argument("--sources",
                        help="comma-separated Hugging Face sources to pull: "
                             + ", ".join(DATASET_SOURCES) + " (needs `pip install datasets`)")
    parser.add_argument("--seed", action="store_true",
                        help="rebuild candidates.jsonl from the verified problems/ tree")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSONL (default: problems/candidates.jsonl)")
    parser.add_argument("--append", action="store_true", help="append to the existing JSONL instead of replacing it")
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

    if args.seed:
        return run_seed(out_path)
    if args.sources:
        return run_datasets(args, out_path)
    if not args.source:
        parser.error("one of --seed, --source or --sources is required")
    return run_extract(args, out_path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
