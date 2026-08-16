#!/usr/bin/env python3
"""Tests for the blueprint generator's LaTeX sanitizer. Stdlib only.

    python problems/test_blueprint.py

Each case is prose from a real dataset entry that rendered wrong at some point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_blueprint import sanitize, texttt  # noqa: E402

# (label, input, must appear in the output, must NOT appear in the output)
CASES: list[tuple[str, str, list[str], list[str]]] = [
    ("display math is not escaped",
     r"Let \[ f(x) = \begin{cases} x^2+9 & \text{if }x<-5, \\ 3x-8& \text{if }x\ge -5. \end{cases} \] Find it.",
     [r"\begin{cases}", r"x^2+9 &", r"\end{cases}"],
     [r"\^{}", r"\&", r"\_"]),

    ("align* environment is not escaped",
     r"Given that \begin{align*} x_{1}&=211,\\ x_{2}&=375, \end{align*} find the value.",
     [r"\begin{align*}", r"x_{1}&=211", r"\end{align*}"],
     [r"x\_", r"\&="]),

    ("inline \\( \\) is math",
     r"For integers \(n\), the value of \(f(n)\) with n_1 & n_2 fixed.",
     [r"\(n\)", r"f(n)"],
     []),

    ("prose around math still gets escaped",
     r"Let $x$ be 50% of the total_amount & nothing else.",
     [r"\%", r"total\_amount", r"\&", "$x$"],
     []),

    ("escaped dollar survives",
     r"Together they have $\$$35. How many more?",
     [r"\$"],
     []),

    ("unbalanced math is closed, not dropped",
     r"The ratio of the number of students is $\frac{3}{4}",
     [r"\frac{3}{4}", "$"],
     []),

    ("html tags are stripped",
     r"Solve the system: <center> \( x + y = a \) </center> where a is constant.",
     [r"\( x + y = a \)"],
     ["<center>", "</center>", "&lt;"]),

    ("caret in prose is escaped",
     r"The value 2^10 written in prose.",
     [r"\^{}"],
     []),

    ("display $$ pairs correctly",
     r"We have $$a & b$$ and then a_b in prose.",
     [r"$$a & b$$", r"a\_b"],
     []),

    ("empty input yields nothing",
     "   ",
     [],
     []),
]


def check_balance(text: str) -> str | None:
    """Return a complaint if math delimiters are left open."""
    if len(re.findall(r"(?<!\\)\$", text)) % 2:
        return "odd number of unescaped $"
    for opener, closer in ((r"\\\[", r"\\\]"), (r"\\\(", r"\\\)")):
        if len(re.findall(opener, text)) != len(re.findall(closer, text)):
            return f"unbalanced {opener}/{closer}"
    for environment in re.findall(r"\\begin\{([A-Za-z]+\*?)\}", text):
        opens = len(re.findall(r"\\begin\{" + re.escape(environment) + r"\}", text))
        closes = len(re.findall(r"\\end\{" + re.escape(environment) + r"\}", text))
        if opens != closes:
            return f"unbalanced {environment} environment"
    return None


def main() -> int:
    failures: list[str] = []
    checks = 0

    for label, raw, expected, forbidden in CASES:
        got = sanitize(raw)
        checks += 1
        if not expected and not forbidden:
            if got is not None:
                failures.append(f"{label}: expected None, got {got!r}")
            continue
        if got is None:
            failures.append(f"{label}: sanitize returned None; nothing may be dropped")
            continue
        for needle in expected:
            if needle not in got:
                failures.append(f"{label}: missing {needle!r} in {got!r}")
        for needle in forbidden:
            if needle in got:
                failures.append(f"{label}: leaked {needle!r} in {got!r}")
        complaint = check_balance(got)
        if complaint:
            failures.append(f"{label}: {complaint} in {got!r}")

    # texttt has to survive the underscores that theorem titles are full of.
    checks += 1
    if texttt("SET_E_01") != r"\texttt{SET\_E\_01}":
        failures.append(f"texttt mangled an id: {texttt('SET_E_01')!r}")

    # Every informal statement in the manifest must come out balanced.
    manifest = Path(__file__).resolve().parent / "candidates.jsonl"
    if manifest.exists():
        import json
        dropped = 0
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            informal = record.get("informal")
            if not informal:
                continue
            got = sanitize(informal)
            if got is None:
                dropped += 1
                continue
            complaint = check_balance(got)
            if complaint:
                failures.append(f"{record.get('source_id')}: {complaint}")
        checks += 1
        if dropped:
            failures.append(f"{dropped} manifest statements were dropped entirely")

    for line in failures[:20]:
        print(f"FAIL  {line}")
    if len(failures) > 20:
        print(f"      ... and {len(failures) - 20} more")
    print(f"\n{checks - min(len(failures), checks)}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
