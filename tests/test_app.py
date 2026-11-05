"""Tests for the local Thought Diff app."""

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from thought_diff_app.__main__ import build_parser, find_examples, main, resolve_launch
from thought_diff_app.errors import SessionError
from thought_diff_app.server import make_server
from thought_diff_app.session import Session
from thought_diff_app.view import DISCLAIMER

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "examples" / "original"
WORKING = ROOT / "examples" / "working"


def _copy_examples(tmp_path: Path) -> tuple[Path, Path]:
    original = tmp_path / "original"
    working = tmp_path / "working"
    shutil.copytree(ORIGINAL, original)
    shutil.copytree(WORKING, working)
    return original, working


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(repo: Path, text: str) -> None:
    repo.mkdir()
    (repo / "a.py").write_text(text, encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Thought Diff Test")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-m", "init")


@pytest.fixture
def client(tmp_path: Path):
    original, working = _copy_examples(tmp_path)
    session = Session()
    session.watching = False
    session.configure(working=working, original=original)
    httpd = make_server(session, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    base = f"http://{host}:{port}"
    try:
        yield base, working, original
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
        session.close()


def _request(method: str, url: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as res:
            body = res.read().decode("utf-8")
            return res.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def test_examples_match_cli_comparer():
    session = Session()
    session.configure(working=WORKING, original=ORIGINAL)
    snap = session.snapshot()
    cli = subprocess.run(
        [sys.executable, "-m", "thought_diff", "compare", str(ORIGINAL), str(WORKING), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(cli.stdout)
    assert snap["code_diff"] == data["code_diff"]
    assert snap["thought_diff"] == data["thought_diff"]
    assert snap["structure_diff"] == data["structure_diff"]
    assert snap["disclaimer"] == data["disclaimer"] == DISCLAIMER
    assert snap["app"]["published"] is False
    assert snap["app"]["comparer_version"] == "0.2.0"
    assert "+ function added: shop:shipping(amount)" in snap["structure_diff"]
    assert snap["counts"]["structure_notes"] == len(snap["structure_diff"])
    assert snap["counts"]["thought_changes"] == sum(
        1 for line in snap["thought_diff"] if line[:1] not in (" ", "\t")
    )
    assert snap["empty"] is False
    view = session.file_view("shop.py")
    assert any("shipping" in (row["working_reading"] or "") for row in view["rows"])
    session.close()


def test_write_updates_reading_and_leaves_original_untouched(tmp_path: Path):
    original, working = _copy_examples(tmp_path)
    before = (original / "shop.py").read_bytes()
    session = Session()
    session.configure(working=working, original=original)
    result = session.write_file(
        "shop.py",
        (working / "shop.py").read_text(encoding="utf-8").replace(
            "return round(amount, 2)", "return round(amount, 3)"
        ),
    )
    joined = "\n".join(result["session"]["thought_diff"])
    assert "round(amount, 2)" in joined
    assert "round(amount, 3)" in joined
    assert (original / "shop.py").read_bytes() == before
    assert "~ body changed: shop:total" in result["session"]["structure_diff"]
    added = session.write_file("extra.py", "def extra():\n    return 1\n")
    assert "+ function added: extra:extra()" in added["session"]["structure_diff"]
    removed = session.delete_file("pricing.py")
    assert not (working / "pricing.py").exists()
    assert (original / "pricing.py").read_text(encoding="utf-8")
    assert any(row["path"] == "pricing.py" and row["status"] == "removed" for row in removed["files"])
    session.close()


def test_external_edit_is_seen_by_poll(tmp_path: Path):
    original, working = _copy_examples(tmp_path)
    session = Session()
    session.configure(working=working, original=original)
    revision = session.snapshot()["revision"]
    target = working / "shop.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# edited outside the app\n", encoding="utf-8")
    assert session.poll() is True
    snap = session.snapshot()
    assert snap["revision"] == revision + 1
    assert "edited outside the app" in snap["code_diff"]
    assert session.poll() is False
    session.close()


def test_path_escape_and_skipped_dirs(tmp_path: Path):
    original, working = _copy_examples(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    (working / "link.py").symlink_to(outside)
    (working / ".git").mkdir()
    (working / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    session = Session()
    session.configure(working=working, original=original)
    with pytest.raises(SessionError):
        session.write_file("../outside.py", "hacked = True\n")
    with pytest.raises(SessionError):
        session.write_file("/etc/passwd", "nope\n")
    with pytest.raises(SessionError):
        session.write_file(".git/config", "nope\n")
    with pytest.raises(SessionError):
        session.write_file("link.py", "hacked = True\n")
    assert outside.read_text(encoding="utf-8") == "secret = 1\n"
    assert all(".git" not in row["path"] for row in session.snapshot()["files"])
    session.close()


def test_git_dir_does_not_dirty_an_otherwise_equal_tree(tmp_path: Path):
    original = tmp_path / "original"
    working = tmp_path / "working"
    original.mkdir()
    working.mkdir()
    text = "x = 1\n"
    (original / "a.py").write_text(text, encoding="utf-8")
    (working / "a.py").write_text(text, encoding="utf-8")
    (working / ".git").mkdir()
    (working / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    session = Session()
    session.configure(working=working, original=original)
    assert session.snapshot()["empty"] is True
    session.close()


def test_non_python_is_code_diff_only(tmp_path: Path):
    original = tmp_path / "original"
    working = tmp_path / "working"
    original.mkdir()
    working.mkdir()
    (original / "notes.txt").write_text("hello\n", encoding="utf-8")
    (working / "notes.txt").write_text("hello there\n", encoding="utf-8")
    session = Session()
    session.configure(working=working, original=original)
    snap = session.snapshot()
    assert "notes.txt" in snap["code_diff"]
    assert not any("function" in line and "notes" in line for line in snap["structure_diff"])
    session.close()


def test_same_folder_warns(tmp_path: Path):
    folder = tmp_path / "same"
    folder.mkdir()
    (folder / "a.py").write_text("x = 1\n", encoding="utf-8")
    session = Session()
    session.configure(working=folder, original=folder)
    assert session.snapshot()["meta"]["same_path_warning"] is True
    session.close()


def test_git_ref_original(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo, "def f():\n    return 1\n")
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    session = Session()
    session.configure(working=repo, git_ref="HEAD")
    snap = session.snapshot()
    joined = "\n".join(snap["thought_diff"])
    assert "returns 1" in joined
    assert "returns 2" in joined
    assert snap["meta"]["original_kind"] == "git"
    assert "~ body changed: a:f" in snap["structure_diff"]
    session.close()


def test_bad_git_ref(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo, "x = 1\n")
    session = Session()
    with pytest.raises(SessionError):
        session.configure(working=repo, git_ref="no-such-ref")
    with pytest.raises(SessionError):
        session.configure(working=repo, git_ref="-rf")
    with pytest.raises(SessionError):
        session.configure(working=repo, git_ref="HEAD;rm")
    session.close()


def test_large_file_is_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("thought_diff_app.paths.MAX_BYTES", 20)
    original = tmp_path / "original"
    working = tmp_path / "working"
    original.mkdir()
    working.mkdir()
    (original / "a.py").write_text("x = 1\n", encoding="utf-8")
    (working / "a.py").write_text("x = 1\n", encoding="utf-8")
    (working / "big.py").write_text("y = 1\n" * 10, encoding="utf-8")
    session = Session()
    session.configure(working=working, original=original)
    snap = session.snapshot()
    assert all(row["path"] != "big.py" for row in snap["files"])
    assert any("big.py" in note for note in snap["meta"]["read_notes"])
    session.close()


def test_http_edit_roundtrip(client):
    base, working, original = client
    status, page = 200, None
    with urlopen(base + "/") as res:
        status = res.status
        html = res.read().decode("utf-8")
    assert status == 200
    assert "Thought diff" in html
    assert "Structure diff" in html
    assert "Code diff" in html
    assert "not recovered thoughts" in html
    assert "Not regeneration" in html
    assert "marketplace" in html
    with urlopen(base + "/static/app.js") as res:
        script = res.read()
    assert b"saveOpenFile" in script
    code, snap = _request("GET", base + "/api/session")
    assert code == 200
    assert snap["app"]["published"] is False
    assert "+ function added: shop:shipping(amount)" in snap["structure_diff"]
    shop = (working / "shop.py").read_text(encoding="utf-8").replace("4.99", "8.00")
    before = (original / "shop.py").read_bytes()
    code, written = _request("PUT", base + "/api/file", {"path": "shop.py", "content": shop})
    assert code == 200
    assert "8.00" in "\n".join(written["session"]["thought_diff"])
    assert (original / "shop.py").read_bytes() == before
    code, missing = _request("PUT", base + "/api/file", {"path": "../nope.py", "content": "x = 1\n"})
    assert code == 400
    assert "error" in missing
    code, still = _request("GET", base + "/api/session")
    assert code == 200
    assert "8.00" in still["code_diff"]
    code, bad = _request("POST", base + "/api/session", {"working": str(working), "original": str(working / "missing")})
    assert code == 400
    code, kept = _request("GET", base + "/api/session")
    assert code == 200
    assert kept["meta"]["working"] == str(working.resolve())
    code, _missing = _request("GET", base + "/nope")
    assert code == 404


def test_http_git_ref_switch(client, tmp_path: Path):
    base, _working, _original = client
    repo = tmp_path / "repo"
    _init_repo(repo, "def f():\n    return 1\n")
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    code, snap = _request("POST", base + "/api/session", {"working": str(repo), "git_ref": "HEAD"})
    assert code == 200
    assert snap["meta"]["original_kind"] == "git"
    assert "returns 2" in "\n".join(snap["thought_diff"])


def test_launch_defaults_and_cli(tmp_path: Path):
    args = build_parser().parse_args([])
    cfg = resolve_launch(args, search_from=ROOT)
    assert cfg["using_examples"] is True
    assert cfg["working"] == WORKING.resolve()
    assert cfg["original"] == ORIGINAL.resolve()
    with pytest.raises(SessionError):
        resolve_launch(build_parser().parse_args(["--working", str(tmp_path)]), search_from=ROOT)
    assert find_examples(tmp_path, include_cwd=False, include_package=False) is None

    help_run = subprocess.run(
        [sys.executable, "-m", "thought_diff_app", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "marketplace" in help_run.stdout
    assert "Not regeneration" in help_run.stdout
    assert "--git-ref" in help_run.stdout

    version = subprocess.run(
        [sys.executable, "-m", "thought_diff_app", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "0.1.0" in version.stdout
    assert "0.2.0" in version.stdout

    check = subprocess.run(
        [sys.executable, "-m", "thought_diff_app", "--check", "--no-browser"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "empty: false" in check.stdout
    assert "thought changes:" in check.stdout

    missing = subprocess.run(
        [sys.executable, "-m", "thought_diff_app", "--check", "--working", str(tmp_path), "--original", str(tmp_path / "nope")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert "does not exist" in missing.stderr
    assert main(["--version"]) == 0
