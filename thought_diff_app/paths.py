"""Working-tree paths and file reads for the app.

The vendored comparer reads every decodable file under a root. The app uses
the same rule, and also skips VCS metadata, virtualenvs, and caches so a git
checkout compared to ``git archive`` is not drowned in ``.git`` files.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import SessionError

SKIP_DIRS = frozenset({
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
})

# Editor writes and comparison reads share this cap.
MAX_BYTES = 2_000_000


def normalize_rel(rel: str) -> str:
    """Return a slash-separated relative path, or raise SessionError."""
    if not isinstance(rel, str) or not rel.strip():
        raise SessionError("A file path is required.")
    rel = rel.strip().replace("\\", "/")
    if rel.startswith("/") or (len(rel) >= 2 and rel[1] == ":"):
        raise SessionError("Use a path relative to the working folder.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts:
        raise SessionError("A file path is required.")
    if any(p == ".." for p in parts):
        raise SessionError("Paths cannot contain '..'.")
    if any(p in SKIP_DIRS for p in parts):
        raise SessionError(
            "That path is outside the compared tree "
            "(.git, caches, and virtualenvs are skipped)."
        )
    return "/".join(parts)


def iter_files(root: Path):
    """Yield ``(path, relative posix path)`` for comparable files under root."""
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            # Atomic editor saves use this prefix and must not show up as files.
            if name.startswith(".thought-diff-write-"):
                continue
            path = Path(dirpath) / name
            if not path.is_file():
                continue
            yield path, path.relative_to(root).as_posix()


def read_tree(root: Path) -> tuple[dict[str, str], list[str]]:
    """Read a source tree the way the app compares it.

    Returns ``(files, notes)``. Notes describe files that were skipped or
    unreadable. Binary files (invalid UTF-8) are skipped quietly, matching
    ``thought_diff.core.load_tree``.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SessionError(f"Not a directory: {root}")
    files: dict[str, str] = {}
    notes: list[str] = []
    for path, rel in iter_files(root):
        try:
            size = path.stat().st_size
        except OSError as exc:
            notes.append(f"{rel}: {exc}")
            continue
        if size > MAX_BYTES:
            notes.append(f"{rel}: skipped (larger than {MAX_BYTES} bytes)")
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            notes.append(f"{rel}: {exc}")
    return files, notes


def file_signature(root: Path) -> tuple:
    """Cheap change token: relative path, mtime, and size.

    Editors bump mtime when they save. A size change is included so a test
    (and a save that lands in the same timestamp tick) still shows up when
    the byte length changes. ``reevaluate`` also runs on demand.
    """
    items = []
    for path, rel in iter_files(root):
        try:
            st = path.stat()
        except OSError:
            items.append((rel, -1, -1))
            continue
        items.append((rel, st.st_mtime_ns, st.st_size))
    return tuple(items)


def working_target(root: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``root`` for a read or write.

    Symlinks that point outside the working folder are rejected so the editor
    cannot write the frozen original or any other directory.
    """
    rel = normalize_rel(rel)
    root = root.resolve()
    candidate = root.joinpath(*rel.split("/"))
    parent = candidate.parent.resolve()
    if parent != root and root not in parent.parents:
        raise SessionError("Path escapes the working folder.")
    if candidate.exists() or candidate.is_symlink():
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise SessionError("Path escapes the working folder.")
        if resolved.exists() and not resolved.is_file():
            raise SessionError("Path is not a file.")
        return resolved
    return parent / candidate.name
