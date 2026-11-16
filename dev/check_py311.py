#!/usr/bin/env python3
"""Check that every submission file parses as Python 3.11 and uses no 3.12+ features.

ast.parse(feature_version=(3, 11)) catches most new syntax; the regex scan catches what it
misses (nested same-quote f-strings are tokenizer-level, and stdlib APIs added after 3.11).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    r"^\s*type\s+\w+\s*=": "PEP 695 type alias",
    r"\bdef\s+\w+\s*\[": "PEP 695 generic function",
    r"\bclass\s+\w+\s*\[": "PEP 695 generic class",
    r"itertools\.batched|\bbatched\(": "itertools.batched (3.12)",
    r"typing\.override|from typing import[^\n]*\boverride\b": "typing.override (3.12)",
    r"\.is_relative_to\(": "ok (3.9) - listed for awareness",
    r"datetime\.UTC\b": "ok (3.11)",
    r"\bPath\.walk\(|\.walk\(\)": "Path.walk (3.12)",
    r"f\"[^\"\n]*\{[^}\n]*\"[^\"\n]*\"[^}\n]*\}": "nested same-quote f-string (3.12)",
    r"f'[^'\n]*\{[^}\n]*'[^'\n]*'[^}\n]*\}": "nested same-quote f-string (3.12)",
}
ALLOWED = {"ok (3.9) - listed for awareness", "ok (3.11)"}


def main() -> int:
    bad = 0
    files = sorted((ROOT / "submission").rglob("*.py"))
    for f in files:
        src = f.read_text(encoding="utf-8")
        try:
            ast.parse(src, filename=str(f), feature_version=(3, 11))
        except SyntaxError as e:
            print("SYNTAX %s:%s %s" % (f.relative_to(ROOT), e.lineno, e.msg))
            bad += 1
        for i, line in enumerate(src.splitlines(), 1):
            for rx, what in PATTERNS.items():
                if what in ALLOWED:
                    continue
                if re.search(rx, line):
                    print("FEATURE %s:%d %s: %s" % (f.relative_to(ROOT), i, what, line.strip()[:80]))
                    bad += 1
    print("%d files checked, %d problems" % (len(files), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
