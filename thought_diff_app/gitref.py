"""Materialize a git ref into a temporary tree (the frozen original)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .errors import GitRefError

# Branch, tag, commit, HEAD~1, origin/main, HEAD@{1}. Not a leading dash,
# which git would treat as a flag.
_REF = re.compile(r"^(?!-)[A-Za-z0-9._/~^@{}+-]+$")


def materialize_git_ref(repo: Path, ref: str) -> Path:
    """Archive ``ref`` from ``repo`` into a new temp directory and return it.

    The caller owns the directory and must delete it. ``git`` and ``tar``
    must be on PATH. The ref is one argv element; it is never passed through
    a shell.
    """
    repo = Path(repo).expanduser().resolve()
    ref = (ref or "").strip()
    if not _REF.fullmatch(ref):
        raise GitRefError(
            "Git ref is empty or contains unsupported characters. "
            "Use a branch, tag, commit, or HEAD (for example main or HEAD~1)."
        )
    if not repo.is_dir():
        raise GitRefError(f"Git repo does not exist: {repo}")
    git = shutil.which("git")
    tar = shutil.which("tar")
    if not git:
        raise GitRefError("git is not on PATH. Install git, or pass an original folder instead.")
    if not tar:
        raise GitRefError("tar is not on PATH. Install tar, or pass an original folder instead.")

    inside = subprocess.run(
        [git, "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0:
        detail = (inside.stderr or inside.stdout or "").strip()
        raise GitRefError(f"Not a git repository: {repo}" + (f"\n{detail}" if detail else ""))

    verify = subprocess.run(
        [git, "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        raise GitRefError(f'Git ref "{ref}" does not resolve to a commit in {repo}.')

    tmp = Path(tempfile.mkdtemp(prefix="thought-diff-orig-"))
    git_proc = subprocess.Popen(
        [git, "-C", str(repo), "archive", "--format=tar", ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    tar_proc = subprocess.Popen(
        [tar, "-x", "-C", str(tmp)],
        stdin=git_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert git_proc.stdout is not None
    git_proc.stdout.close()
    git_err_parts: list[bytes] = []
    tar_err_parts: list[bytes] = []

    def _drain(pipe, sink: list[bytes]) -> None:
        if pipe is not None:
            sink.append(pipe.read())

    git_thread = threading.Thread(target=_drain, args=(git_proc.stderr, git_err_parts))
    tar_thread = threading.Thread(target=_drain, args=(tar_proc.stderr, tar_err_parts))
    git_thread.start()
    tar_thread.start()
    tar_code = tar_proc.wait()
    git_code = git_proc.wait()
    git_thread.join()
    tar_thread.join()
    git_err = b"".join(git_err_parts).decode("utf-8", "replace")
    tar_err = b"".join(tar_err_parts).decode("utf-8", "replace")
    if git_code != 0 or tar_code != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        detail = (git_err or tar_err or f"git archive failed ({git_code}/{tar_code})").strip()
        raise GitRefError(f'Could not read git ref "{ref}": {detail}')
    return tmp
