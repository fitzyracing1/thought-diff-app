"""Local Thought Diff app.

Re-evaluates a working folder against a frozen original (a directory or a git
ref). Interpretations come from the vendored thought_diff comparer (Python AST),
not from a model, and the app does not regenerate code.
"""

__version__ = "0.1.0"
