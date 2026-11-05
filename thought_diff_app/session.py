"""One local comparison: a working folder against a frozen original."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import SessionError
from .gitref import materialize_git_ref
from .paths import MAX_BYTES, file_signature, normalize_rel, read_tree, working_target
from .view import (
    APP_NOTE,
    DISCLAIMER,
    LIMITS,
    aligned_readings,
    evaluate,
    public_snapshot,
)


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` via a temp file so a reader never sees a half-written file."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".thought-diff-write-")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Session:
    """Holds the last evaluation and can re-read the working folder.

    The original folder (or the temp tree from a git ref) is only read.
    Writes go to the working folder, and only when the editor asks.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.working: Path | None = None
        self.original_dir: Path | None = None
        self.git_ref = ""
        self.repo: Path | None = None
        self.watching = True
        self._git_tmp: Path | None = None
        self._signature: tuple | None = None
        self._revision = 0
        self._last: dict[str, Any] | None = None
        self._orig_tree: dict[str, str] | None = None
        self._work_tree: dict[str, str] | None = None
        self._sa = None
        self._sb = None
        self._same_path = False
        self._display = ""
        self._kind = "folder"
        self._original_folder = ""
        self._read_notes: list[str] = []

    def close(self) -> None:
        with self._lock:
            tmp = self._git_tmp
            self._git_tmp = None
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    def configure(
        self,
        *,
        working: str | Path,
        original: str | Path | None = None,
        git_ref: str | None = None,
        repo: str | Path | None = None,
    ) -> dict[str, Any]:
        """Point the session at a working folder and a frozen original.

        A git ref wins over ``original``, matching the editor extension.
        On failure the previous session is left in place.
        """
        ref = (git_ref or "").strip()
        if repo is not None and not str(repo).strip():
            repo = None
        if original is not None and not str(original).strip():
            original = None
        if not str(working).strip():
            raise SessionError("A working folder is required.")
        work_path = Path(working).expanduser().resolve()
        if not work_path.is_dir():
            raise SessionError(f"Working folder does not exist: {work_path}")

        new_tmp: Path | None = None
        repo_path: Path | None = None
        original_folder = ""
        try:
            if ref:
                repo_path = Path(repo).expanduser().resolve() if repo else work_path
                new_tmp = materialize_git_ref(repo_path, ref)
                original_dir = new_tmp
                kind = "git"
                display = f"git:{ref} @ {repo_path}"
            else:
                if not original:
                    raise SessionError("Set a frozen original folder, or a git ref.")
                original_dir = Path(original).expanduser().resolve()
                if not original_dir.is_dir():
                    raise SessionError(f"Original folder does not exist: {original_dir}")
                kind = "folder"
                display = str(original_dir)
                original_folder = str(original_dir)
            orig_tree, orig_notes = read_tree(original_dir)
            work_tree, work_notes = read_tree(work_path)
            same = kind == "folder" and original_dir == work_path
            snap, sa, sb = self._assemble(
                orig_tree,
                work_tree,
                orig_notes + work_notes,
                working=work_path,
                display=display,
                kind=kind,
                git_ref=ref,
                repo=repo_path,
                original_folder=original_folder,
                same=same,
                error=None,
            )
        except Exception:
            if new_tmp is not None:
                shutil.rmtree(new_tmp, ignore_errors=True)
            raise

        with self._lock:
            old_tmp = self._git_tmp
            self._git_tmp = new_tmp
            self.working = work_path
            self.original_dir = original_dir
            self.git_ref = ref
            self.repo = repo_path
            self._same_path = same
            self._display = display
            self._kind = kind
            self._original_folder = original_folder
            self._orig_tree = orig_tree
            self._work_tree = work_tree
            self._sa = sa
            self._sb = sb
            self._read_notes = orig_notes + work_notes
            self._revision += 1
            snap["revision"] = self._revision
            self._last = snap
            self._signature = file_signature(work_path)
        if old_tmp is not None and old_tmp != new_tmp:
            shutil.rmtree(old_tmp, ignore_errors=True)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._last is None:
                raise SessionError("Nothing compared yet.")
            return _copy_snapshot(self._last)

    def poll(self) -> bool:
        """Re-evaluate when the working folder's files changed. Return True if so."""
        with self._lock:
            if self.working is None:
                return False
            signature = file_signature(self.working)
            if signature == self._signature:
                return False
            self._reevaluate_locked()
            return True

    def reevaluate(self) -> dict[str, Any]:
        with self._lock:
            self._reevaluate_locked()
        return self.snapshot()

    def _reevaluate_locked(self) -> None:
        """Re-read both trees and publish. Caller holds ``self._lock``."""
        working = self.working
        original = self.original_dir
        if working is None or original is None:
            raise SessionError("Set a working folder and a frozen original (folder or git ref).")
        try:
            orig_tree, orig_notes = read_tree(original)
            work_tree, work_notes = read_tree(working)
            snap, sa, sb = self._assemble(
                orig_tree,
                work_tree,
                orig_notes + work_notes,
                working=working,
                display=self._display,
                kind=self._kind,
                git_ref=self.git_ref,
                repo=self.repo,
                original_folder=self._original_folder,
                same=self._same_path,
                error=None,
            )
            signature = file_signature(working)
        except Exception as exc:
            self._revision += 1
            base = dict(self._last or _empty_snapshot())
            base["revision"] = self._revision
            base["error"] = f"Re-evaluation failed: {exc}"
            meta = dict(base.get("meta") or {})
            meta["evaluated_at"] = _now()
            base["meta"] = meta
            self._last = base
            try:
                self._signature = file_signature(working)
            except OSError:
                pass
            return

        self._revision += 1
        snap["revision"] = self._revision
        self._last = snap
        self._orig_tree = orig_tree
        self._work_tree = work_tree
        self._sa = sa
        self._sb = sb
        self._read_notes = orig_notes + work_notes
        self._signature = signature

    def file_view(self, rel: str) -> dict[str, Any]:
        rel = normalize_rel(rel)
        with self._lock:
            if self._orig_tree is None or self._work_tree is None:
                raise SessionError("Nothing compared yet.")
            if rel not in self._orig_tree and rel not in self._work_tree:
                raise SessionError(f"File is not in the comparison: {rel}")
            original = self._orig_tree.get(rel, "")
            working = self._work_tree.get(rel, "")
            sa, sb = self._sa, self._sb
            exists_original = rel in self._orig_tree
            exists_working = rel in self._work_tree
        return {
            "path": rel,
            "original": original,
            "working": working,
            "exists_original": exists_original,
            "exists_working": exists_working,
            "rows": aligned_readings(rel, original, working, sa, sb),
        }

    def write_file(self, rel: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise SessionError("File content must be a string.")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise SessionError("File is larger than 2 MB.")
        with self._lock:
            path = self._target_locked(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, content)
            self._reevaluate_locked()
        try:
            view = self.file_view(rel)
        except SessionError:
            view = None
        return {"session": self.snapshot(), "file": view}

    def delete_file(self, rel: str) -> dict[str, Any]:
        with self._lock:
            path = self._target_locked(rel)
            if not path.is_file():
                raise SessionError(f"File not found in the working folder: {normalize_rel(rel)}")
            path.unlink()
            self._reevaluate_locked()
        return self.snapshot()

    def _target_locked(self, rel: str) -> Path:
        root = self.working
        if root is None:
            raise SessionError("No working folder configured.")
        return working_target(root, rel)

    def _assemble(
        self,
        orig_tree,
        work_tree,
        notes,
        *,
        working: Path,
        display: str,
        kind: str,
        git_ref: str,
        repo: Path | None,
        original_folder: str,
        same: bool,
        error: str | None,
    ):
        sa, sb, code, thought, structure = evaluate(orig_tree, work_tree)
        with self._lock:
            watching = self.watching
        meta = {
            "working": str(working),
            "original_display": display,
            "original_kind": kind,
            "original_folder": original_folder,
            "git_ref": git_ref,
            "repo": str(repo) if repo else "",
            "evaluated_at": _now(),
            "same_path_warning": same,
            "read_notes": list(notes),
            "watching": watching,
            "limits": LIMITS,
            "app_note": APP_NOTE,
            "disclaimer": DISCLAIMER,
        }
        snap = public_snapshot(
            orig=orig_tree,
            work=work_tree,
            code=code,
            thought=thought,
            structure=structure,
            sa=sa,
            sb=sb,
            meta=meta,
            error=error,
        )
        return snap, sa, sb


def _copy_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    # Shallow copy of the top level plus the lists a caller might append to.
    out = dict(snap)
    out["files"] = [dict(row) for row in snap.get("files", [])]
    out["thought_diff"] = list(snap.get("thought_diff", []))
    out["structure_diff"] = list(snap.get("structure_diff", []))
    out["structure_original"] = list(snap.get("structure_original", []))
    out["structure_working"] = list(snap.get("structure_working", []))
    out["counts"] = dict(snap.get("counts", {}))
    out["meta"] = dict(snap.get("meta", {}))
    out["meta"]["read_notes"] = list(out["meta"].get("read_notes") or [])
    out["app"] = dict(snap.get("app", {}))
    return out


def _empty_snapshot() -> dict[str, Any]:
    return {
        "empty": True,
        "code_diff": "",
        "thought_diff": [],
        "structure_diff": [],
        "structure_original": [],
        "structure_working": [],
        "disclaimer": DISCLAIMER,
        "app_note": APP_NOTE,
        "files": [],
        "counts": {"files_differ": 0, "thought_changes": 0, "structure_notes": 0},
        "meta": {},
        "error": None,
        "app": {"name": "thought-diff-app", "published": False},
    }
