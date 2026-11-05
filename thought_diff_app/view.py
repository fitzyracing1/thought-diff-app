"""Turn two source trees into the three diffs the app shows.

All readings come from ``thought_diff``. This module does not invent an
explanation the comparer did not compute.
"""

from __future__ import annotations

import difflib
from typing import Any

from thought_diff import __version__ as COMPARER_VERSION
from thought_diff.core import (
    code_diff,
    interpret_lines,
    structure_diff,
    structure_lines,
    structure_of,
    thought_diff,
)

from . import __version__ as APP_VERSION

# Same sentence the thought_diff CLI puts in --json. Tests lock the two together.
DISCLAIMER = (
    "Interpretations are deterministic AST readings of the code as it is now, "
    "not recovered thoughts of the model that wrote it. Not regeneration."
)

APP_NOTE = (
    "The app re-reads the working copy when it changes. "
    "It does not call a model and does not rewrite code on its own."
)

LIMITS = (
    "Structure and line readings are computed for Python files. "
    "Other text files are included in the code diff and read as plain text. "
    "Files under .git, __pycache__, .pytest_cache, node_modules, .venv, and venv "
    "are skipped, as are files larger than 2 MB."
)


def evaluate(orig: dict[str, str], work: dict[str, str]):
    """Return ``(structure_original, structure_working, code, thought, structure)``."""
    sa = structure_of(orig)
    sb = structure_of(work)
    return (
        sa,
        sb,
        code_diff(orig, work),
        thought_diff(orig, work, sa, sb),
        structure_diff(sa, sb),
    )


def file_rows(orig: dict[str, str], work: dict[str, str]) -> list[dict[str, str]]:
    order = {"modified": 0, "added": 1, "removed": 2, "unmodified": 3}
    rows = []
    for path in set(orig) | set(work):
        if path not in orig:
            status = "added"
        elif path not in work:
            status = "removed"
        elif orig[path] != work[path]:
            status = "modified"
        else:
            status = "unmodified"
        rows.append({"path": path, "status": status})
    rows.sort(key=lambda row: (order[row["status"]], row["path"]))
    return rows


def aligned_readings(path: str, original: str, working: str, sa, sb) -> list[dict[str, Any]]:
    """Line up original and working readings for one file.

    Uses the same ``interpret_lines`` strings as the thought diff. ``op`` is
    ``equal``, ``reinterpreted``, ``replace``, ``insert``, or ``delete``.
    """
    ia = interpret_lines(path, original, sa)
    ib = interpret_lines(path, working, sb)
    la = original.splitlines()
    lb = working.splitlines()
    rows: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        span = (i2 - i1) if op == "equal" else max(i2 - i1, j2 - j1)
        for k in range(span):
            oi, nj = i1 + k, j1 + k
            has_old = oi < i2
            has_new = nj < j2
            old_reading = ia[oi] if has_old and oi < len(ia) else None
            new_reading = ib[nj] if has_new and nj < len(ib) else None
            kind = "reinterpreted" if op == "equal" and old_reading != new_reading else op
            rows.append({
                "op": kind,
                "original_line": oi + 1 if has_old else None,
                "working_line": nj + 1 if has_new else None,
                "original_text": la[oi] if has_old else None,
                "working_text": lb[nj] if has_new else None,
                "original_reading": old_reading,
                "working_reading": new_reading,
            })
    return rows


def _thought_change_count(lines: list[str]) -> int:
    return sum(1 for line in lines if line[:1] not in (" ", "\t"))


def public_snapshot(
    *,
    orig: dict[str, str],
    work: dict[str, str],
    code: str,
    thought: list[str],
    structure: list[str],
    sa,
    sb,
    meta: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    files = file_rows(orig, work)
    empty = not (code or thought or structure)
    return {
        "empty": empty,
        "code_diff": code,
        "thought_diff": thought,
        "structure_diff": structure,
        "structure_original": structure_lines(sa),
        "structure_working": structure_lines(sb),
        "disclaimer": DISCLAIMER,
        "app_note": APP_NOTE,
        "files": files,
        "counts": {
            "files_differ": sum(1 for row in files if row["status"] != "unmodified"),
            "thought_changes": _thought_change_count(thought),
            "structure_notes": len(structure),
        },
        "meta": meta,
        "error": error,
        "app": {
            "name": "thought-diff-app",
            "version": APP_VERSION,
            "comparer_version": COMPARER_VERSION,
            "published": False,
        },
    }
