# Thought Diff app

A local app that compares a working folder to a **frozen copy of AI-written code** and shows three diffs side by side:

1. **Code diff** — unified diff, working copy against the frozen original.
2. **Thought diff** — for each changed line, the line's reading before and after (what it does, which function it sits in, what it calls, who calls it). Unchanged lines whose reading changed, such as a function that gained a caller, are included and marked `unchanged text, reinterpreted`.
3. **Structure diff** — modules, classes, function signatures, imports, and calls. A function moved or renamed with the same body is reported as a move or rename.

Edits are **re-evaluated**. The app reads the working copy again and runs the comparer again. It does not regenerate code, and it does not recover or display hidden model thoughts. Readings come from the Python AST heuristics in [`thought_diff`](https://github.com/fitzyracing1/thought-diff) v0.2.0, vendored in this repo. Check them against the code and the tests. They can be shallow or wrong.

This is the companion to the Cursor / VS Code extension in [fitzyracing1/thought-diff](https://github.com/fitzyracing1/thought-diff). That repo is the extension. This repo is the standalone app. The extension is left unchanged.

## What it is not

- **Not regeneration.** Nothing here rewrites the working copy except the editor when you type.
- **Not hidden model thoughts.** A reading is a current structured look at the code, computed from the AST.
- **Not published.** This app is not published to the VS Code Marketplace, Open VSX, or any Cursor extension marketplace. You run it yourself from a clone. The extension in the other repo is also not marketplace-published; install that one from a local VSIX if you want it inside the editor.
- **Not a Python runtime for every language.** Structure and line readings cover Python. Other text files still appear in the code diff and are read as plain text. Binary files are ignored. Files larger than 2 MB are skipped. `.git`, `__pycache__`, `.pytest_cache`, `node_modules`, `.venv`, and `venv` are skipped so a checkout compared with `git archive` is not flooded with VCS metadata.

## Install and run

Requires **Python 3.10+**. The app has no runtime dependencies. `git` and `tar` are required only when the frozen original is a git ref. `pytest` is required only to run the tests.

```bash
git clone https://github.com/fitzyracing1/thought-diff-app
cd thought-diff-app
python3 -m thought_diff_app
```

With no paths, the app compares `examples/original` (frozen) with `examples/working` (a small shop example where shipping was added) and opens `http://127.0.0.1:8765/`. Pass `--no-browser` to skip opening a browser.

The page is a working-copy editor plus the three diffs. Typing writes that file into the working folder (debounced, or immediately with Ctrl+S / Cmd+S) and re-evaluates. The frozen original is never written.

Point it at your own trees:

```bash
python3 -m thought_diff_app --original /path/to/frozen --working /path/to/project
python3 -m thought_diff_app --git-ref HEAD --working /path/to/project
python3 -m thought_diff_app --git-ref main --repo /path/to/repo --working /path/to/work
```

`--git-ref` wins when both a folder and a ref are set, same as the extension. The ref is one of a branch, tag, commit, `HEAD`, or `HEAD~1`. The repo defaults to the working folder. The ref is archived with `git archive` into a temp directory; that directory is the frozen original.

Useful flags:

| Flag | Meaning |
|---|---|
| `--host` | Bind address. Default `127.0.0.1`. |
| `--port` | Default `8765`. |
| `--no-browser` | Do not open a browser. |
| `--no-watch` | Do not watch the working folder. Use **Re-evaluate** in the page. |
| `--check` | Evaluate once, print counts, and exit. |
| `--version` | App version and vendored comparer version. |

The server can read the original and read or write the working folder. Keep the default bind on localhost. A non-local host prints a warning.

### Editing in Cursor at the same time

Leave this app running on the working folder. Saves in Cursor (or any editor) show up here within a couple of seconds: the watcher re-reads the folder and re-evaluates. Edits made in this app's editor are written to that same folder, so Cursor may ask to reload the file. Either editor is the working copy. Neither one is asked to regenerate the code.

You can also change folders from the form at the top of the page. A bad path returns an error and keeps the comparison you already had.

### Command-line comparer

The vendored comparer is the same CLI as thought-diff v0.2.0:

```bash
python3 -m thought_diff compare examples/original examples/working
python3 -m thought_diff compare ORIGINAL WORKING --json
python3 -m thought_diff compare ORIGINAL WORKING --exit-code
python3 -m thought_diff structure examples/working
```

The app calls that library in-process (`compare`, line interpretations, and structure diff). It does not shell out on each keystroke. The CLI walks every decodable file except `__pycache__`. The app additionally skips the directories listed above and files over 2 MB, so an app result can be quieter than the CLI on a git checkout.

## Tests

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest
```

CI runs those tests on Python 3.10 and 3.12, the example comparison, and `python -m thought_diff_app --check`.

## Limits

- Python AST only, for thought and structure. Syntax errors are reported (a `!` line in the structure diff) and do not crash the comparison.
- Readings are heuristics: a line's role, its scope, calls, and callers. They are not a proof, and they are not the model's private reasoning.
- Git-ref originals need `git` and `tar`, and the ref must resolve to a commit.
- The in-app editor writes the whole file. An external save that lands while a debounced write is in flight can be overwritten by that write.
- There is no account, no model API, and no extension host. It is a local web page served by this process.

## License

This repository does not include a license.
