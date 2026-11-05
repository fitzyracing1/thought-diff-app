import subprocess
import sys
from pathlib import Path

from thought_diff import compare, interpret_lines, structure_of

ROOT = Path(__file__).resolve().parents[1]
BASE = {
    "app.py": "from util import helper\n\n\ndef run(x):\n    y = helper(x)\n    return y + 1\n\n\ndef unused():\n    return 0\n",
    "util.py": "def helper(v):\n    return v * 2\n",
}


def edit(**changes):
    d = dict(BASE)
    for k, v in changes.items():
        k = k.replace("_py", ".py")
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def test_unchanged_is_empty():
    r = compare(BASE, dict(BASE))
    assert r.empty and r.code_diff == "" and r.thought_diff == [] and r.structure_diff == []


def test_example_dirs_identical_to_self():
    assert compare(ROOT / "examples/original", ROOT / "examples/original").empty


def test_line_edit_updates_interpretation_and_structure():
    w = edit(app_py=BASE["app.py"].replace("return y + 1", "return max(y, 1)"))
    r = compare(BASE, w)
    td = "\n".join(r.thought_diff)
    assert "returns y + 1" in td and "returns max(y, 1)" in td and "calls max" in td
    assert "~ body changed: app:run" in r.structure_diff
    assert "+ call: app:run -> max" in r.structure_diff
    assert "-    return y + 1" in r.code_diff


def test_add_function_shows_in_structure_and_reinterprets_context():
    w = edit(app_py=BASE["app.py"] + "\n\ndef main():\n    return unused()\n")
    r = compare(BASE, w)
    assert "+ function added: app:main()" in r.structure_diff
    td = "\n".join(r.thought_diff)
    # the unchanged `def unused` line is reinterpreted: now it has a caller
    assert "unchanged text, reinterpreted" in td
    assert "not called anywhere" in td and "called by app:main" in td


def test_remove_function_shows_in_structure():
    w = edit(app_py=BASE["app.py"].split("\n\ndef unused")[0] + "\n")
    r = compare(BASE, w)
    assert "- function removed: app:unused" in r.structure_diff


def test_rename_detected_as_structure_not_just_text():
    w = edit(util_py="def double(v):\n    return v * 2\n",
             app_py=BASE["app.py"].replace("helper", "double"))
    r = compare(BASE, w)
    assert "~ function renamed: util:helper -> util:double (same body)" in r.structure_diff
    assert "- import: app <- util.helper" in r.structure_diff
    assert "+ import: app <- util.double" in r.structure_diff


def test_move_between_modules_detected():
    w = edit(util_py=None, helpers_py="def helper(v):\n    return v * 2\n",
             app_py=BASE["app.py"].replace("from util", "from helpers"))
    r = compare(BASE, w)
    assert "~ function moved: util:helper -> helpers:helper (same body)" in r.structure_diff
    assert "- module: util" in r.structure_diff and "+ module: helpers" in r.structure_diff


def test_signature_change():
    w = edit(util_py="def helper(v, k):\n    return v * k\n")
    assert "~ signature changed: util:helper(v) -> (v, k)" in compare(BASE, w).structure_diff


def test_syntax_error_reported_not_crash():
    w = edit(util_py="def helper(v:\n")
    r = compare(BASE, w)
    assert any(n.startswith("! util: syntax error") for n in r.structure_diff)


def test_interpret_lines_scope_and_callers():
    s = structure_of(BASE)
    ia = interpret_lines("app.py", BASE["app.py"], s)
    assert ia[0] == "imports helper from util [in app (top level)]"
    assert ia[3].startswith("defines function run(x); not called anywhere")
    assert ia[4] == "sets y = helper(x) [in app:run] calls helper"


def test_cli_runs_on_examples():
    out = subprocess.run([sys.executable, "-m", "thought_diff", "compare",
                          "examples/original", "examples/working", "--exit-code"],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 1
    assert "+ function added: shop:shipping(amount)" in out.stdout
    assert "not recovered thoughts" in out.stdout


def test_cli_json_output():
    out = subprocess.run(
        [sys.executable, "-m", "thought_diff", "compare",
         "examples/original", "examples/working", "--json"],
        cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0
    data = __import__("json").loads(out.stdout)
    assert data["empty"] is False
    assert "disclaimer" in data
    assert any("shipping" in line for line in data["structure_diff"])
