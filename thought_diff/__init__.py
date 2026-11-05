"""thought-diff: compare edits to a frozen copy of AI-written code, plus how
line and program-structure interpretations change. Interpretations are
deterministic AST heuristics, not recovered model thoughts.

Vendored from thought-diff v0.2.0
(https://github.com/fitzyracing1/thought-diff) for the companion app.
The comparison behavior is that release: AST readings, not model thoughts.
"""
__version__ = "0.2.0"
from .core import compare, interpret_lines, structure_of  # noqa: F401
