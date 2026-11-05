"""Launch the local Thought Diff app."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from thought_diff import __version__ as COMPARER_VERSION

from . import __version__
from .errors import SessionError
from .server import serve
from .session import Session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thought_diff_app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Local Thought Diff app: compare a working folder to a frozen original "
            "(folder or git ref) and show the code diff, the interpretation diff, and "
            "the structure diff. Live re-evaluation only. Not regeneration, not hidden "
            "model thoughts, and not published to an extension marketplace."
        ),
        epilog=(
            "examples:\n"
            "  python -m thought_diff_app\n"
            "  python -m thought_diff_app --original ./frozen --working ./project\n"
            "  python -m thought_diff_app --git-ref HEAD --working ./project\n"
            "  python -m thought_diff compare ORIGINAL WORKING\n"
        ),
    )
    parser.add_argument("--original", help="Frozen original folder (the AI copy). Ignored when --git-ref is set.")
    parser.add_argument("--working", help="Working folder you are editing.")
    parser.add_argument("--git-ref", help="Git ref used as the frozen original (branch, tag, or commit).")
    parser.add_argument("--repo", help="Git repo for --git-ref. Defaults to the working folder.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="Port (default 8765).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    parser.add_argument("--no-watch", action="store_true", help="Do not watch the working folder; use Re-evaluate.")
    parser.add_argument("--check", action="store_true", help="Evaluate once, print a summary, and exit.")
    parser.add_argument("--version", action="store_true", help="Print the app and comparer versions.")
    return parser


def find_examples(search_from: Path | None = None, *, include_cwd: bool = True, include_package: bool = True) -> tuple[Path, Path] | None:
    roots: list[Path] = []
    if search_from is not None:
        roots.append(Path(search_from))
    if include_cwd:
        roots.append(Path.cwd())
    if include_package:
        roots.append(Path(__file__).resolve().parents[1])
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        original = root / "examples" / "original"
        working = root / "examples" / "working"
        if original.is_dir() and working.is_dir():
            return original, working
    return None


def resolve_launch(args, search_from: Path | None = None) -> dict:
    """Choose folders from CLI flags, or the bundled examples when nothing was passed."""
    working = args.working
    original = args.original
    git_ref = (args.git_ref or "").strip()
    repo = args.repo
    if not working and not original and not git_ref:
        found = find_examples(search_from)
        if found is None:
            raise SessionError(
                "Pass --working and either --original or --git-ref. "
                "Example trees were not found under examples/."
            )
        original_path, working_path = found
        return {
            "working": working_path,
            "original": original_path,
            "git_ref": None,
            "repo": None,
            "using_examples": True,
        }
    if not working:
        raise SessionError("Pass --working (the folder you are editing).")
    if git_ref:
        return {
            "working": working,
            "original": None,
            "git_ref": git_ref,
            "repo": repo,
            "using_examples": False,
        }
    if not original:
        raise SessionError("Pass --original or --git-ref for the frozen AI copy.")
    return {
        "working": working,
        "original": original,
        "git_ref": None,
        "repo": repo,
        "using_examples": False,
    }


def print_summary(snap: dict) -> None:
    meta = snap.get("meta") or {}
    counts = snap.get("counts") or {}
    print(f"original: {meta.get('original_display', '')}")
    print(f"working: {meta.get('working', '')}")
    print(f"files differ: {counts.get('files_differ', 0)}")
    print(f"thought changes: {counts.get('thought_changes', 0)}")
    print(f"structure notes: {counts.get('structure_notes', 0)}")
    print(f"empty: {str(bool(snap.get('empty'))).lower()}")
    if snap.get("error"):
        print(f"error: {snap['error']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10+ is required.", file=sys.stderr)
        return 1
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"thought-diff-app {__version__}")
        print(f"thought_diff comparer {COMPARER_VERSION}")
        return 0
    if not (1 <= args.port <= 65535):
        print("Port must be between 1 and 65535.", file=sys.stderr)
        return 2
    try:
        cfg = resolve_launch(args)
    except SessionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    session = Session()
    session.watching = not args.no_watch
    try:
        session.configure(
            working=cfg["working"],
            original=cfg["original"],
            git_ref=cfg["git_ref"],
            repo=cfg["repo"],
        )
    except SessionError as exc:
        print(str(exc), file=sys.stderr)
        session.close()
        return 2

    if args.check:
        try:
            print_summary(session.snapshot())
        finally:
            session.close()
        return 0

    host = args.host
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "Warning: listening beyond localhost. This app can read and write the working folder.",
            file=sys.stderr,
        )
    url = f"http://{host}:{args.port}/"
    meta = session.snapshot()["meta"]
    print("Thought Diff")
    print(f"  {url}")
    print(f"  original: {meta['original_display']}")
    print(f"  working:  {meta['working']}")
    if cfg["using_examples"]:
        print("  using the bundled examples (examples/original vs examples/working)")
    if args.no_watch:
        print("  Watching is off. Use Re-evaluate in the app.")
    else:
        print("  Watching the working folder. Edits re-evaluate; nothing is regenerated.")
    print("  Not published to an extension marketplace.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        serve(session, host, args.port, watch=not args.no_watch)
    except OSError as exc:
        print(f"Could not listen on {host}:{args.port}: {exc}", file=sys.stderr)
        return 2
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
