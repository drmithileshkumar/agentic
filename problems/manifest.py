#!/usr/bin/env python3
"""Browse and edit the candidate manifest (candidates.jsonl).

Stdlib only. No third-party dependencies, no pandas.

Commands:
  list      table of candidates, filterable by domain / tier / status
  stats     domain x tier coverage matrix (verified / total)
  show      full record for one id, statement included
  verify    flip verified: true on an id
  unverify  flip verified: false on an id
  promote   write a verified candidate into problems/{DOMAIN}/{TIER}/ and
            mark it verified
  add       add a candidate by hand from a .lean file
  check     sanity-check the manifest against the problems/ tree

See problems/SETUP_COMPLETE.md for the full workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROBLEMS_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = PROBLEMS_DIR / "candidates.jsonl"

DOMAINS = [
    "SET", "TOP", "ALG", "ABA", "LIN", "NUM",
    "RAN", "CAN", "CMB", "GEO", "PRB",
]
TIERS = ["E", "M", "H"]


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load(path: Path) -> list[dict]:
    if not path.exists():
        print(f"error: {path} does not exist. Run extract_candidates.py --seed first.",
              file=sys.stderr)
        raise SystemExit(1)
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"error: {path.name}:{lineno} is not valid JSON: {exc}", file=sys.stderr)
            raise SystemExit(1)
    return records


def save(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_one(records: list[dict], ident: str) -> dict:
    """Look an entry up by id (full or unique prefix) or by theorem name."""
    hits = [r for r in records if r.get("id") == ident or r.get("name") == ident]
    if not hits:
        hits = [r for r in records if str(r.get("id", "")).startswith(ident)]
    if not hits:
        print(f"error: no candidate matching '{ident}'", file=sys.stderr)
        raise SystemExit(1)
    if len(hits) > 1:
        ids = ", ".join(r["id"] for r in hits[:5])
        print(f"error: '{ident}' is ambiguous ({len(hits)} matches: {ids} ...)", file=sys.stderr)
        raise SystemExit(1)
    return hits[0]


# ---------------------------------------------------------------------------
# Table rendering (stdlib only)
# ---------------------------------------------------------------------------

def truncate(value: str, width: int) -> str:
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value if len(value) <= width else value[: width - 1] + "…"


def render_table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    """ASCII table. aligns entries are 'l' or 'r'; defaults to all left."""
    if not rows:
        rows = [["-"] * len(headers)]
    aligns = aligns or ["l"] * len(headers)
    cells = [[str(c) for c in row] for row in rows]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in cells))
        for i in range(len(headers))
    ]

    def rule() -> str:
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(values: list[str]) -> str:
        out = []
        for i, value in enumerate(values):
            out.append(value.rjust(widths[i]) if aligns[i] == "r" else value.ljust(widths[i]))
        return "| " + " | ".join(out) + " |"

    parts = [rule(), line(headers), rule()]
    parts.extend(line(row) for row in cells)
    parts.append(rule())
    return "\n".join(parts)


def status_of(record: dict) -> str:
    return "verified" if record.get("verified") else "pending"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args, records: list[dict]) -> int:
    rows = []
    for record in sorted(records, key=lambda r: (r.get("domain", ""), r.get("tier", ""), r.get("name", ""))):
        if args.domain and record.get("domain") != args.domain:
            continue
        if args.tier and record.get("tier") != args.tier:
            continue
        if args.verified and not record.get("verified"):
            continue
        if args.pending and record.get("verified"):
            continue
        if args.source and args.source.lower() not in str(record.get("source_dataset", "")).lower():
            continue
        rows.append([
            record.get("id", ""),
            record.get("domain", ""),
            record.get("tier", ""),
            status_of(record),
            truncate(record.get("name", ""), 28),
            truncate(record.get("statement", ""), args.width),
        ])
        if args.limit and len(rows) >= args.limit:
            break

    print(render_table(
        ["id", "dom", "tier", "status", "name", "statement"],
        rows,
    ))
    print(f"{len(rows)} shown / {len(records)} in manifest")
    return 0


def cmd_stats(args, records: list[dict]) -> int:
    counts: dict[tuple[str, str], list[int]] = {}
    for record in records:
        key = (record.get("domain", "UNK"), record.get("tier", "?"))
        slot = counts.setdefault(key, [0, 0])
        slot[1] += 1
        if record.get("verified"):
            slot[0] += 1

    domains = [d for d in DOMAINS if any(k[0] == d for k in counts)]
    domains += sorted({k[0] for k in counts} - set(DOMAINS))
    if not domains:
        domains = DOMAINS

    rows = []
    for domain in domains:
        row = [domain]
        total_v = total_n = 0
        for tier in TIERS:
            verified, total = counts.get((domain, tier), [0, 0])
            total_v += verified
            total_n += total
            row.append(f"{verified}/{total}" if total else "-")
        row.append(f"{total_v}/{total_n}")
        rows.append(row)

    grand_v = sum(1 for r in records if r.get("verified"))
    rows.append(["ALL"] + [
        f"{sum(counts.get((d, t), [0, 0])[0] for d in domains)}/"
        f"{sum(counts.get((d, t), [0, 0])[1] for d in domains)}"
        for t in TIERS
    ] + [f"{grand_v}/{len(records)}"])

    print(render_table(
        ["domain", "E", "M", "H", "total"],
        rows,
        aligns=["l", "r", "r", "r", "r"],
    ))
    print("cells are verified/total")

    on_disk = sorted(PROBLEMS_DIR.rglob("*.lean"))
    print(f"\n.lean files in problems/: {len(on_disk)}")
    return 0


def cmd_show(args, records: list[dict]) -> int:
    record = find_one(records, args.id)
    width = max(len(k) for k in record)
    for key, value in record.items():
        if key in ("statement", "proof"):
            continue
        print(f"{key.ljust(width)} : {value}")
    print("\n--- statement ---")
    print(record.get("statement", ""))
    if record.get("proof"):
        print("\n--- proof ---")
        print(record["proof"])
    return 0


def _set_verified(args, records: list[dict], value: bool) -> int:
    record = find_one(records, args.id)
    record["verified"] = value
    if args.note:
        record["notes"] = args.note
    save(Path(args.manifest), records)
    print(f"{record['id']} ({record.get('name')}) -> verified: {value}")
    return 0


def cmd_verify(args, records):    return _set_verified(args, records, True)
def cmd_unverify(args, records):  return _set_verified(args, records, False)


def next_index(domain: str, tier: str) -> int:
    """Lowest unused NN for problems/{domain}/{tier}/."""
    folder = PROBLEMS_DIR / domain / tier
    used = set()
    if folder.is_dir():
        for lean_file in folder.glob(f"{domain}_{tier}_*.lean"):
            match = re.search(r"_(\d+)\.lean$", lean_file.name)
            if match:
                used.add(int(match.group(1)))
    index = 1
    while index in used:
        index += 1
    return index


def existing_index(record: dict, domain: str, tier: str) -> int | None:
    """The NN this record was already promoted to, if that file still fits."""
    path = record.get("problem_path")
    if not path:
        return None
    name = Path(path).name
    match = re.fullmatch(rf"{domain}_{tier}_(\d+)\.lean", name)
    if match and (PROBLEMS_DIR.parent / path).exists():
        return int(match.group(1))
    return None


def cmd_promote(args, records: list[dict]) -> int:
    record = find_one(records, args.id)
    domain = args.domain or record.get("domain", "")
    tier = args.tier or record.get("tier", "")

    if domain not in DOMAINS:
        print(f"error: '{domain}' is not a known domain. Pass --domain.", file=sys.stderr)
        return 1
    if tier not in TIERS:
        print(f"error: '{tier}' is not a known tier. Pass --tier.", file=sys.stderr)
        return 1

    if args.index:
        index = args.index
    else:
        # Re-promoting an entry that already has a file rewrites that file
        # rather than allocating a second copy under a new number.
        index = existing_index(record, domain, tier) or next_index(domain, tier)
    problem_id = f"{domain}_{tier}_{index:02d}"
    folder = PROBLEMS_DIR / domain / tier
    target = folder / f"{problem_id}.lean"
    if target.exists() and not args.force:
        print(f"error: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1

    statement = record.get("statement", "").strip()
    # Rename the declaration to match the file's problem id.
    original = record.get("name", "")
    if original:
        statement = re.sub(
            r"^((?:theorem|lemma)\s+)" + re.escape(original) + r"\b",
            r"\1" + problem_id,
            statement,
        )
    proof = (record.get("proof") or "").strip() or "by\n  sorry"

    imports = record.get("imports") or ["Mathlib"]
    header = "\n".join(f"import {imp}" for imp in imports)
    dataset = record.get("source_dataset", "unknown")
    source_file = record.get("source_file", "")
    will_be_verified = bool(record.get("verified")) or args.verify
    status = "verified ✅" if will_be_verified else "UNVERIFIED — elaborate before use"

    content = (
        f"{header}\n\n"
        f"-- {domain}-{tier}-{index:02d}: {record.get('name', '')}\n"
        f"-- Source: {dataset}"
        + (f" ({source_file})" if source_file else "")
        + f"\n-- Difficulty: {tier}\n"
        f"-- Status: {status}\n"
        f"-- Manifest id: {record.get('id', '')}\n\n"
        f"{statement} := {proof}\n"
    )

    if args.dry_run:
        print(f"would write {target}:\n")
        print(content)
        return 0

    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")

    record["domain"] = domain
    record["tier"] = tier
    record["problem_path"] = target.relative_to(PROBLEMS_DIR.parent).as_posix()
    if args.verify:
        record["verified"] = True
    save(Path(args.manifest), records)

    print(f"wrote {target}")
    if "sorry" in proof:
        print("note: promoted with `sorry` — fill in the proof before flipping verified")
    return 0


def cmd_add(args, records: list[dict]) -> int:
    sys.path.insert(0, str(PROBLEMS_DIR))
    from extract_candidates import iter_declarations, make_record  # noqa: E402

    path = Path(args.file).resolve()
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 1

    known = {r.get("id") for r in records}
    added = 0
    for name, statement, proof, imports in iter_declarations(path):
        record = make_record(
            name=name,
            statement=statement,
            proof=proof,
            imports=imports,
            dataset=args.source or "manual",
            source_file=path.name,
            domain=args.domain,
            tier=args.tier,
            verified=False,
            notes=args.note or "added by hand",
        )
        if record["id"] in known:
            print(f"skipped duplicate {record['id']} ({name})")
            continue
        records.append(record)
        known.add(record["id"])
        added += 1
        print(f"added {record['id']}  {args.domain}/{args.tier}  {name}")

    if added:
        save(Path(args.manifest), records)
    print(f"{added} added, manifest now has {len(records)} entries")
    return 0


INDEX_MODULE = PROBLEMS_DIR / "Problems.lean"


def check_index(records: list[dict]) -> list[str]:
    """Cross-check problems/Problems.lean against the manifest's verified set.

    The index is what `lake build` follows, so a verified problem missing from
    it is a problem nothing compiles — which is exactly the state this whole
    arrangement exists to prevent.
    """
    if not INDEX_MODULE.exists():
        return [f"{INDEX_MODULE.name} is missing; `lake build` will not "
                f"compile any problem"]

    imported = set(re.findall(r"^import\s+(\S+)", INDEX_MODULE.read_text(encoding="utf-8"),
                              re.MULTILINE))
    issues = []
    expected = set()
    for record in records:
        if not record.get("verified"):
            continue
        path = record.get("problem_path") or ""
        if not path.startswith("problems/") or not path.endswith(".lean"):
            continue
        module = path[len("problems/"):-len(".lean")].replace("/", ".")
        expected.add(module)
        if module not in imported:
            issues.append(f"{record.get('id')}: verified but {module} is not "
                          f"imported by {INDEX_MODULE.name}")

    for module in sorted(imported - expected):
        issues.append(f"{INDEX_MODULE.name} imports {module}, which is not a "
                      f"verified entry in the manifest")
    return issues


def cmd_check(args, records: list[dict]) -> int:
    problems = []
    ids, dupes = set(), []
    for record in records:
        ident = record.get("id")
        if ident in ids:
            dupes.append(ident)
        ids.add(ident)
        if record.get("domain") not in DOMAINS:
            problems.append(f"{ident}: unknown domain '{record.get('domain')}'")
        if record.get("tier") not in TIERS:
            problems.append(f"{ident}: unknown tier '{record.get('tier')}'")
        path = record.get("problem_path")
        if path and not (PROBLEMS_DIR.parent / path).exists():
            problems.append(f"{ident}: problem_path missing on disk -> {path}")
        if record.get("verified") and not path:
            problems.append(f"{ident}: verified but has no problem_path")
        if "sorry" in (record.get("proof") or "") and record.get("verified"):
            problems.append(f"{ident}: verified but proof contains `sorry`")
    for ident in dupes:
        problems.append(f"duplicate id in manifest: {ident}")

    tracked = {r.get("problem_path") for r in records if r.get("problem_path")}
    for lean_file in sorted(PROBLEMS_DIR.rglob("*.lean")):
        if lean_file == INDEX_MODULE:
            continue
        rel = lean_file.relative_to(PROBLEMS_DIR.parent).as_posix()
        if rel not in tracked:
            problems.append(f"on disk but not in manifest: {rel}")

    problems.extend(check_index(records))

    if not problems:
        print(f"OK — {len(records)} entries, no issues found.")
        return 0
    for issue in problems:
        print(f"  ! {issue}")
    print(f"\n{len(problems)} issue(s) found.")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse and edit candidates.jsonl.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                        help="path to the JSONL manifest (default: problems/candidates.jsonl)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="table of candidates")
    p.add_argument("--domain", choices=DOMAINS)
    p.add_argument("--tier", choices=TIERS)
    p.add_argument("--verified", action="store_true", help="verified entries only")
    p.add_argument("--pending", action="store_true", help="unverified entries only")
    p.add_argument("--source", help="substring match on source_dataset")
    p.add_argument("--limit", type=int)
    p.add_argument("--width", type=int, default=60, help="statement column width")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats", help="domain x tier coverage matrix")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("show", help="full record for one id")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("verify", help="mark an entry verified")
    p.add_argument("id")
    p.add_argument("--note")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("unverify", help="mark an entry unverified")
    p.add_argument("id")
    p.add_argument("--note")
    p.set_defaults(func=cmd_unverify)

    p = sub.add_parser("promote", help="write a candidate into the problems/ tree")
    p.add_argument("id")
    p.add_argument("--domain", choices=DOMAINS, help="override the record's domain")
    p.add_argument("--tier", choices=TIERS, help="override the record's tier")
    p.add_argument("--index", type=int, help="force the NN suffix (default: next free)")
    p.add_argument("--verify", action="store_true", help="also mark it verified")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.add_argument("--dry-run", action="store_true", help="print the file instead of writing it")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("add", help="add candidates from a .lean file by hand")
    p.add_argument("file")
    p.add_argument("--domain", choices=DOMAINS, required=True)
    p.add_argument("--tier", choices=TIERS, required=True)
    p.add_argument("--source", help="source_dataset label")
    p.add_argument("--note")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("check", help="sanity-check manifest vs the problems/ tree")
    p.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    records = load(Path(args.manifest))
    return args.func(args, records)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
