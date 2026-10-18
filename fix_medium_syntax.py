"""
Repair Medium- (or Hard-) tier statements that fail to elaborate (see check_medium_parse.py).

Only the IDs listed in the tier's *_parse_failures.txt are touched. Three fixes, in order:

  1. Big-operator syntax: `∑ i in s` / `∏ i in s` -> `∑ i ∈ s` (current Mathlib
     rejects `in`).
  2. Factorial: a statement using postfix `!` (`5!`, `n!`, `(n + 1)!`) gets
     `open Nat in`, which brings the notation into scope.
  3. Dataset `open` headers: extract_candidates.py keeps only the `import`
     lines of each source header and drops the `open` lines, so short names
     (`card`, `span`, `Tendsto`, `closure`, ...) resolve wrongly or not at all.
     The header comes from the cached HF dataset row the entry was harvested
     from, so it is the source's own namespace choice, not a guess.

Opens are written as `open ... in` prefixes so the statement stays a single
command; run_proofs.py appends ` := by sorry` exactly as before.

    python fix_medium_syntax.py            # apply (backs up candidates.jsonl first)
    python fix_medium_syntax.py --tier H   # same, for the Hard tier
    python fix_medium_syntax.py --dry-run  # report counts only
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

PROBLEMS   = Path(r"C:\lean\MyMathlibProject\problems")
CANDIDATES = PROBLEMS / "candidates.jsonl"
# Per tier: (flagged-IDs file from check_medium_parse.py, backup taken before writing).
TIERS = {
    "M": (Path(r"C:\lean\medium_parse_failures.txt"), PROBLEMS / "candidates_backup_oct6.jsonl"),
    "H": (Path(r"C:\lean\hard_parse_failures.txt"),   PROBLEMS / "candidates_backup_oct8_preH.jsonl"),
}

BIGOP_IN  = re.compile(r"([∑∏]\s*\w+)\s+in\s+")
FACTORIAL = re.compile(r"[\w)]!")
OPEN_LINE = re.compile(r"^open\s+(scoped\s+)?(.+)$", re.M)
OPEN_IN   = re.compile(r"^open\s+(scoped\s+)?(.+?)\s+in\n")

HEADER_SOURCES = {
    "ProofNetSharp": ("PAug/ProofNetSharp", "lean4_src_header"),
    "miniF2F":       ("cat-searcher/minif2f-lean4", "header"),
}


def flagged_ids(failures: Path) -> set[str]:
    return set(re.findall(r"^=== (\S+)", failures.read_text(encoding="utf-8"), re.M))


def load_headers() -> dict[tuple[str, str], str]:
    """(source_dataset, source_id) -> header, from the local HF cache only."""
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from datasets import load_dataset  # noqa: PLC0415

    headers = {}
    for label, (hf_id, field) in HEADER_SOURCES.items():
        ds = load_dataset(hf_id)
        for split in ds:
            for row in ds[split]:
                headers[(label, str(row["id"]))] = row[field] or ""
    return headers


def opened(statement: str) -> set[tuple[bool, str]]:
    """Namespaces already opened by the statement's `open ... in` prefixes."""
    out = set()
    rest = statement
    while m := OPEN_IN.match(rest):
        out |= {(bool(m.group(1)), ns) for ns in m.group(2).split()}
        rest = rest[m.end():]
    return out


def add_opens(statement: str, wanted: list[tuple[bool, str]]) -> str:
    have = opened(statement)
    new = [w for w in wanted if w not in have]
    plain  = [ns for scoped, ns in new if not scoped]
    scoped = [ns for is_scoped, ns in new if is_scoped]
    prefix = ""
    if plain:
        prefix += f"open {' '.join(plain)} in\n"
    if scoped:
        prefix += f"open scoped {' '.join(scoped)} in\n"
    return prefix + statement


def header_opens(header: str) -> list[tuple[bool, str]]:
    out = []
    for m in OPEN_LINE.finditer(header):
        out += [(bool(m.group(1)), ns) for ns in m.group(2).split()]
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tier", choices=sorted(TIERS), default="M")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    failures, backup = TIERS[args.tier]

    ids = flagged_ids(failures)
    lines = CANDIDATES.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) if l.strip() else None for l in lines]
    targets = [r for r in records if r and r["id"] in ids and r.get("tier") == args.tier]
    print(f"{len(ids)} flagged IDs; {len(targets)} tier-{args.tier} records found")

    # 1. Big-operator `in` -> `∈`
    fixed_bigop = 0
    for r in targets:
        new = BIGOP_IN.sub(r"\1 ∈ ", r["statement"])
        if new != r["statement"]:
            r["statement"] = new
            fixed_bigop += 1
    print(f"Step 1 (∑/∏ ... in -> ∈):  {fixed_bigop} statements changed")

    # 2. Factorial notation
    fixed_fact = 0
    for r in targets:
        if FACTORIAL.search(r["statement"]) and (False, "Nat") not in opened(r["statement"]):
            r["statement"] = add_opens(r["statement"], [(False, "Nat")])
            fixed_fact += 1
    print(f"Step 2 (open Nat for n!):  {fixed_fact} statements changed")

    # 3. Source `open` headers
    headers = load_headers()
    fixed_hdr, no_header = 0, []
    for r in targets:
        header = headers.get((r.get("source_dataset", ""), r.get("source_id", "")))
        if header is None:
            no_header.append(r.get("name", r["id"]))
            continue
        new = add_opens(r["statement"], header_opens(header))
        if new != r["statement"]:
            r["statement"] = new
            fixed_hdr += 1
    print(f"Step 3 (dataset open headers): {fixed_hdr} statements changed")
    if no_header:
        print(f"  no source header found for {len(no_header)}: {', '.join(no_header)}")

    if args.dry_run:
        print("Dry run: candidates.jsonl not written")
        return
    if not backup.exists():
        shutil.copy2(CANDIDATES, backup)
        print(f"Backed up original to {backup}")
    else:
        print(f"Backup already exists, left as is: {backup}")
    out = [json.dumps(r, ensure_ascii=False) if r else "" for r in records]
    CANDIDATES.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {CANDIDATES}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
