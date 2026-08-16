#!/usr/bin/env python3
"""Generate the blueprint content from candidates.jsonl.

Stdlib only. Writes `blueprint/src/content.tex`, which `blueprint/src/web.tex`
inputs. Re-run it whenever candidates.jsonl changes:

    python problems/gen_blueprint.py
    cd blueprint && leanblueprint web

Layout: one \\section per domain and one \\subsection per difficulty tier, one
theorem block per manifest entry. All 11 domains and all 3 tiers are always
emitted, empty or not, so the gaps in coverage stay visible.

Verified entries get `\\lean{...}` and `\\leanok`. Unverified candidates get
neither — `\\leanok` means "formalized and proved in Lean", and `\\lean{}`
feeds `blueprint/lean_decls`, which `leanblueprint checkdecls` verifies against
the actual Lean build. Emitting either for an unproved candidate would make the
blueprint claim something false, so unverified entries instead carry their
source dataset and problem id in the theorem title.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROBLEMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROBLEMS_DIR.parent
MANIFEST = PROBLEMS_DIR / "candidates.jsonl"
OUT = REPO_ROOT / "blueprint" / "src" / "content.tex"

# All 11 domains, in the order of the table in problems/README.md.
DOMAIN_NAMES = [
    ("SET", "Set Theory"),
    ("TOP", "Topology"),
    ("ALG", "Algebra"),
    ("ABA", "Abstract Algebra"),
    ("LIN", "Linear Algebra"),
    ("NUM", "Number Theory"),
    ("RAN", "Real Analysis"),
    ("CAN", "Complex Analysis"),
    ("CMB", "Combinatorics"),
    ("GEO", "Geometry"),
    ("PRB", "Probability"),
]
UNKNOWN_DOMAIN = "UNK"

TIER_NAMES = [
    ("E", "Easy"),
    ("M", "Medium"),
    ("H", "Hard"),
]

# The 9 hand-verified seed problems were extracted from Lean source and so have
# no natural-language statement in the manifest. These are theirs.
SEED_DESCRIPTIONS = {
    "SET_E_01": r"For any sets $A$ and $B$, $A \cap B = B \cap A$.",
    "SET_E_02": r"For any sets $A$, $B$, $C$: "
                r"$A \cap (B \cup C) = (A \cap B) \cup (A \cap C)$.",
    "SET_E_03": r"Subset transitivity: if $A \subseteq B$ and $B \subseteq C$ "
                r"then $A \subseteq C$.",
    "SET_E_04": r"Three-step subset chain: if "
                r"$A \subseteq B \subseteq C \subseteq D$ then $A \subseteq D$.",
    "TOP_E_01": r"A function is continuous if and only if preimages of open "
                r"sets are open.",
    "TOP_E_02": r"The composition of continuous functions is continuous.",
    "ALG_E_01": r"In a group, $(ab)^{-1} = b^{-1}a^{-1}$.",
    "ALG_E_02": r"Group homomorphisms preserve inverses: "
                r"$f(a^{-1}) = f(a)^{-1}$.",
    "ALG_E_03": r"Ring identity: $(a+b)(a-b) = a^2 - b^2$.",
}

# Dependency edges between the seed problems, kept from the hand-written
# blueprint so the dependency graph is not just disconnected dots.
SEED_USES = {
    "SET_E_04": ["SET_E_03"],
    "TOP_E_02": ["TOP_E_01"],
    "ALG_E_02": ["ALG_E_01"],
}

# Environments whose bodies are math and must pass through unescaped. Dataset
# prose reaches for these constantly — piecewise definitions arrive as
# `\begin{cases}`, simultaneous equations as `\begin{align*}` — and escaping the
# `&` and `_` inside them is what used to render as `x\_ {1}\& =211`.
MATH_ENVIRONMENTS = frozenset({
    "align", "alignat", "aligned", "array", "bmatrix", "cases", "eqnarray",
    "equation", "gather", "gathered", "matrix", "pmatrix", "smallmatrix",
    "split", "vmatrix", "Bmatrix", "Vmatrix", "subequations",
})

# Ordered alternation: `$$` must be tried before `$`, and an escaped `\$` before
# either, or a display-math opener reads as two empty inline runs.
TOKEN_RE = re.compile(
    r"(?P<escaped>\\[\\$&%#_{}~^])"
    r"|(?P<display>\$\$)"
    r"|(?P<inline>\$)"
    r"|(?P<open>\\\[|\\\()"
    r"|(?P<close>\\\]|\\\))"
    r"|(?P<begin>\\begin\{(?P<benv>[A-Za-z]+\*?)\})"
    r"|(?P<end>\\end\{(?P<eenv>[A-Za-z]+\*?)\})"
)

HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*\s*/?>")


def escape_text(segment: str) -> str:
    """Escape LaTeX specials in a non-math run of dataset prose."""
    segment = re.sub(r"(?<!\\)%", r"\\%", segment)
    segment = re.sub(r"(?<!\\)&", r"\\&", segment)
    segment = re.sub(r"(?<!\\)#", r"\\#", segment)
    segment = re.sub(r"(?<!\\)_", r"\\_", segment)
    segment = re.sub(r"(?<!\\)\^", r"\\^{}", segment)
    return segment


def sanitize(text: str) -> str | None:
    """Make dataset prose safe to drop into LaTeX. None only if there is none.

    Dataset statements are arbitrary LaTeX written by many hands. Everything in
    math mode passes through untouched and the prose between it is escaped —
    where "math mode" means `$...$`, `$$...$$`, `\\[...\\]`, `\\(...\\)` and the
    environments in MATH_ENVIRONMENTS, not `$` alone.

    Unbalanced input is closed rather than discarded. Some source statements are
    simply truncated mid-formula; dropping the whole statement loses more than
    closing the delimiter does.
    """
    text = HTML_TAG_RE.sub(" ", text or "").strip()
    if not text:
        return None

    out: list[str] = []
    stack: list[str] = []          # what it takes to close each open math run
    pos = 0

    for match in TOKEN_RE.finditer(text):
        run = text[pos:match.start()]
        out.append(run if stack else escape_text(run))
        pos = match.end()
        token = match.group(0)

        if match.group("escaped"):
            out.append(token)
            continue

        if match.group("display") or match.group("inline"):
            closer = "$$" if match.group("display") else "$"
            if stack and stack[-1] == closer:
                stack.pop()
            else:
                stack.append(closer)
            out.append(token)
        elif match.group("open"):
            stack.append("\\]" if token == "\\[" else "\\)")
            out.append(token)
        elif match.group("close"):
            if stack and stack[-1] == token:
                stack.pop()
            out.append(token)
        elif match.group("begin"):
            environment = match.group("benv")
            # MATH_ENVIRONMENTS lists base names; `align*` is still align.
            if environment.rstrip("*") in MATH_ENVIRONMENTS:
                stack.append(rf"\end{{{environment}}}")
            out.append(token)
        else:  # \end{...}
            if stack and stack[-1] == token:
                stack.pop()
            out.append(token)

    tail = text[pos:]
    out.append(tail if stack else escape_text(tail))

    # Close whatever the source left hanging, innermost first.
    while stack:
        out.append(stack.pop())

    result = " ".join("".join(out).split())
    # A stray \\ at the end of a theorem body upsets plasTeX.
    return result.rstrip("\\") or None


def texttt(value: str) -> str:
    """A \\texttt{...} with underscores escaped."""
    return r"\texttt{" + value.replace("\\", "").replace("_", r"\_") + "}"


def load_records() -> list[dict]:
    if not MANIFEST.exists():
        print(f"error: {MANIFEST} not found. Run extract_candidates.py first.",
              file=sys.stderr)
        raise SystemExit(1)
    records = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def theorem_block(record: dict, label_for_name: dict[str, str]) -> str:
    name = record.get("name", "")
    verified = bool(record.get("verified"))
    dataset = record.get("source_dataset", "")
    source_id = record.get("source_id", "")

    if verified and name in SEED_DESCRIPTIONS:
        body = SEED_DESCRIPTIONS[name]
    else:
        body = sanitize(record.get("informal", ""))
    if not body:
        body = ("Formal statement only; see the Lean declaration "
                + texttt(name or record.get("id", "")) + ".")

    if verified:
        title = texttt(name)
    elif source_id:
        title = f"{dataset} {texttt(source_id)}"
    else:
        title = dataset or "candidate"

    lines = [rf"\begin{{theorem}}[{title}]",
             rf"  \label{{thm:{record['id']}}}"]

    if verified:
        # Only proved declarations may claim a Lean name or a Lean checkmark.
        if name:
            lines.append(rf"  \lean{{{name}}}")
        lines.append(r"  \leanok")
        uses = [label_for_name[u] for u in SEED_USES.get(name, [])
                if u in label_for_name]
        if uses:
            lines.append(r"  \uses{" + ", ".join(f"thm:{u}" for u in uses) + "}")
    elif name:
        lines.append(rf"  Lean declaration: {texttt(name)}.")

    lines.append(f"  {body}")
    lines.append(r"\end{theorem}")
    return "\n".join(lines)


def coverage_table(buckets: dict[str, dict[str, list[dict]]],
                   domains: list[tuple[str, str]]) -> str:
    """A domain x tier table of counts, so the gaps are legible at a glance."""
    header = " & ".join(["\\textbf{Domain}"]
                        + [rf"\textbf{{{t}}}" for _c, t in TIER_NAMES]
                        + [r"\textbf{Total}", r"\textbf{Verified}"])
    rows = [r"\begin{tabular}{lrrrrr}", r"\hline", header + r" \\", r"\hline"]

    column_totals = {code: 0 for code, _t in TIER_NAMES}
    grand = grand_verified = 0
    for code, title in domains:
        tiers = buckets.get(code, {})
        counts = [len(tiers.get(tier_code, [])) for tier_code, _t in TIER_NAMES]
        for (tier_code, _t), value in zip(TIER_NAMES, counts):
            column_totals[tier_code] += value
        row_total = sum(counts)
        row_verified = sum(1 for entries in tiers.values()
                           for r in entries if r.get("verified"))
        grand += row_total
        grand_verified += row_verified
        rows.append(" & ".join([title] + [str(c) for c in counts]
                               + [str(row_total), str(row_verified)]) + r" \\")

    rows.append(r"\hline")
    rows.append(" & ".join([r"\textbf{All}"]
                           + [str(column_totals[c]) for c, _t in TIER_NAMES]
                           + [rf"\textbf{{{grand}}}", rf"\textbf{{{grand_verified}}}"])
                + r" \\")
    rows.append(r"\hline")
    rows.append(r"\end{tabular}")
    return "\n".join(rows)


def main() -> int:
    records = load_records()
    label_for_name = {r["name"]: r["id"] for r in records if r.get("name")}

    buckets: dict[str, dict[str, list[dict]]] = {}
    for record in records:
        domain = record.get("domain") or UNKNOWN_DOMAIN
        tier = record.get("tier") or "M"
        buckets.setdefault(domain, {}).setdefault(tier, []).append(record)

    total = len(records)
    verified = sum(1 for r in records if r.get("verified"))

    domains = DOMAIN_NAMES + (
        [(UNKNOWN_DOMAIN, "Unclassified")] if UNKNOWN_DOMAIN in buckets else []
    )

    out = [
        "% AUTO-GENERATED by problems/gen_blueprint.py — do not edit by hand.",
        "% Regenerate with:  python problems/gen_blueprint.py",
        "%",
        f"% {total} entries from problems/candidates.jsonl, {verified} verified.",
        "",
        r"\chapter{The benchmark}",
        "",
        f"This part of the blueprint is generated from "
        f"\\texttt{{problems/candidates.jsonl}}, which currently holds {total} "
        f"entries, of which {verified} are verified in Lean. Verified results "
        r"carry a Lean declaration link and a checkmark; the rest are candidate "
        r"statements awaiting formalization and proof.",
        "",
        r"Problems are filed by subject and by difficulty tier. The tier is "
        r"assigned by the rubric in \texttt{problems/extract\_candidates.py}: "
        r"anything drawn from a competition (IMO, Putnam, AIME) is Hard by "
        r"construction, and everything else is scored on the structure of its "
        r"Lean statement --- how much context it hands you, how deeply it is "
        r"quantified, how long it is. It is a proxy, and it is computed from the "
        r"problem rather than from the name of the file it arrived in.",
        "",
        coverage_table(buckets, domains),
        "",
    ]

    # One chapter per domain, one section per tier. This is what splits the site
    # into pages of a readable size: as one chapter it was a single 312 KB page
    # for Algebra. It also gives the per-chapter dependency graphs something
    # smaller than the whole benchmark to draw.
    for code, title in domains:
        tiers = buckets.get(code, {})
        count = sum(len(v) for v in tiers.values())
        done = sum(1 for v in tiers.values() for r in v if r.get("verified"))
        out.append(rf"\chapter{{{title}}}")
        out.append("")
        out.append(f"{count} entries, {done} verified."
                   if count else "No candidates yet for this domain.")
        out.append("")

        # Every tier gets a section, empty or not, so gaps stay visible.
        for tier_code, tier_title in TIER_NAMES:
            entries = tiers.get(tier_code, [])
            out.append(rf"\section{{{title} --- {tier_title}}}")
            out.append("")
            if not entries:
                out.append("No entries yet.")
                out.append("")
                continue
            entries.sort(key=lambda r: (not r.get("verified"),
                                        r.get("source_id") or r.get("name") or ""))
            for record in entries:
                out.append(theorem_block(record, label_for_name))
                out.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")

    skipped = sum(
        1 for r in records
        if not r.get("verified") and r.get("informal") and not sanitize(r["informal"])
    )
    print(f"Wrote {OUT}")
    print(f"  {total} theorem blocks across {len(DOMAIN_NAMES)} domains "
          f"({verified} verified)")
    if skipped:
        print(f"  {skipped} had unbalanced LaTeX in their source prose and "
              f"fell back to a Lean-declaration-only body")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
