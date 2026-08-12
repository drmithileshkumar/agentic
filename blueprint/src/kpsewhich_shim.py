#!/usr/bin/env python3
"""Minimal stand-in for TeX Live's `kpsewhich`, for building the blueprint
without a LaTeX installation.

plasTeX shells out to `kpsewhich` to locate every input file. On Windows it
invokes it through the shell, so a missing `kpsewhich` does not raise — the
subprocess just returns empty output and plasTeX's own fallback path search is
skipped, making it fail to find even `web.tex` sitting in the current
directory. Pointing `[general] kpsewhich` in plastex.cfg at this script
restores the lookup.

It resolves only files that actually exist in the current directory or on
TEXINPUTS. Style files (`blueprint.sty`, `amsmath.sty`, ...) are deliberately
*not* resolved: plasTeX falls back to its own Python implementations of those
packages, which is what we want for the HTML build.

Prints the resolved absolute path and exits 0, or prints nothing and exits 1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def search_dirs() -> list[Path]:
    dirs = [Path.cwd()]
    for entry in os.environ.get("TEXINPUTS", "").split(os.pathsep):
        if entry:
            dirs.append(Path(entry))
    seen, out = set(), []
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            out.append(directory)
    return out


def find(name: str) -> Path | None:
    # kpsewhich is called both with and without the .tex extension.
    names = [name] if Path(name).suffix else [name, name + ".tex"]
    for directory in search_dirs():
        for candidate in names:
            path = directory / candidate
            if path.is_file():
                return path.resolve()
    return None


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        return 1
    hit = find(args[0])
    if hit is None:
        return 1
    print(hit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
