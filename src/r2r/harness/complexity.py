"""Code complexity for the simplifier (D85): statements plus call sites, from the stdlib AST.

Fewer statements means less code to read; fewer call sites means fewer layers and repeated
calls. Comments, blank lines and formatting don't count.
"""

from __future__ import annotations

import ast
from pathlib import Path


def complexity(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return 0
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        total += sum(isinstance(n, ast.stmt | ast.Call) for n in ast.walk(tree))
    return total
