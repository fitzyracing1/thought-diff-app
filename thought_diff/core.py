"""Core: code diff, line interpretations, structure views, and their diffs.

Every "interpretation" here is a *current structured reading* of the code,
computed from the AST. It is not, and does not claim to be, the hidden
reasoning of whatever model wrote the original code. Check it against the
actual code and tests.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


def load_tree(root: str | Path) -> dict[str, str]:
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            try:
                out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
    return out


# ---------------------------------------------------------------- code diff
def code_diff(orig: dict[str, str], work: dict[str, str]) -> str:
    chunks = []
    for path in sorted(set(orig) | set(work)):
        a, b = orig.get(path), work.get(path)
        if a == b:
            continue
        chunks.extend(difflib.unified_diff(
            (a or "").splitlines(keepends=True), (b or "").splitlines(keepends=True),
            fromfile=f"original/{path}" if a is not None else "/dev/null",
            tofile=f"working/{path}" if b is not None else "/dev/null"))
    return "".join(c if c.endswith("\n") else c + "\n" for c in chunks)


# ---------------------------------------------------------------- structure
def _callee(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _body_fingerprint(fn: ast.AST) -> str:
    """Hash of a def's args+body, ignoring its name, for rename/move detection."""
    clone = ast.parse(ast.unparse(fn)).body[0]
    clone.name = "_"
    clone.decorator_list = []
    return hashlib.sha1(ast.dump(clone).encode()).hexdigest()[:12]


@dataclass
class Structure:
    modules: set[str] = field(default_factory=set)
    imports: set[tuple[str, str]] = field(default_factory=set)        # (module, imported)
    functions: dict[str, dict] = field(default_factory=dict)           # "mod:qual" -> info
    classes: dict[str, dict] = field(default_factory=dict)
    calls: set[tuple[str, str]] = field(default_factory=set)           # (caller, callee name)
    errors: dict[str, str] = field(default_factory=dict)


def _module_name(path: str) -> str:
    return path[:-3].replace("/", ".")


def structure_of(tree: dict[str, str]) -> Structure:
    s = Structure()
    for path, text in tree.items():
        if not path.endswith(".py"):
            continue
        mod = _module_name(path)
        s.modules.add(mod)
        try:
            t = ast.parse(text)
        except SyntaxError as e:
            s.errors[mod] = f"syntax error line {e.lineno}: {e.msg}"
            continue

        def visit(node, scope: list[str]):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, ast.Import):
                    for a in ch.names:
                        s.imports.add((mod, a.name))
                elif isinstance(ch, ast.ImportFrom):
                    for a in ch.names:
                        s.imports.add((mod, f"{ch.module or '.'}.{a.name}"))
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = ".".join(scope + [ch.name])
                    s.functions[f"{mod}:{q}"] = {
                        "args": [a.arg for a in ch.args.args],
                        "line": ch.lineno, "fp": _body_fingerprint(ch), "name": ch.name}
                    for sub in ast.walk(ch):
                        if isinstance(sub, ast.Call) and _callee(sub):
                            s.calls.add((f"{mod}:{q}", _callee(sub)))
                    visit(ch, scope + [ch.name])
                elif isinstance(ch, ast.ClassDef):
                    q = ".".join(scope + [ch.name])
                    s.classes[f"{mod}:{q}"] = {
                        "bases": [ast.unparse(b) for b in ch.bases], "line": ch.lineno}
                    visit(ch, scope + [ch.name])
                elif not scope and isinstance(ch, ast.Call):
                    pass
                else:
                    visit(ch, scope)
        visit(t, [])
        for node in t.body:  # top-level calls
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and _callee(sub):
                        s.calls.add((f"{mod}:<module>", _callee(sub)))
    return s


def structure_lines(s: Structure) -> list[str]:
    out = [f"module {m}" for m in sorted(s.modules)]
    out += [f"import {m} <- {i}" for m, i in sorted(s.imports)]
    out += [f"class {k}({', '.join(v['bases'])})" for k, v in sorted(s.classes.items())]
    out += [f"def {k}({', '.join(v['args'])})" for k, v in sorted(s.functions.items())]
    out += [f"call {a} -> {b}" for a, b in sorted(s.calls)]
    out += [f"error {m}: {e}" for m, e in sorted(s.errors.items())]
    return out


def structure_diff(a: Structure, b: Structure) -> list[str]:
    notes = []
    removed = {k: v for k, v in a.functions.items() if k not in b.functions}
    added = {k: v for k, v in b.functions.items() if k not in a.functions}
    for rk, rv in list(removed.items()):
        for ak, av in list(added.items()):
            if rv["fp"] == av["fp"]:
                rmod, amod = rk.split(":")[0], ak.split(":")[0]
                kind = "renamed" if rv["name"] != av["name"] else "moved"
                if rv["name"] != av["name"] and rmod != amod:
                    kind = "renamed+moved"
                notes.append(f"~ function {kind}: {rk} -> {ak} (same body)")
                del removed[rk], added[ak]
                break
    notes += [f"- function removed: {k}" for k in sorted(removed)]
    notes += [f"+ function added: {k}({', '.join(v['args'])})" for k, v in sorted(added.items())]
    for k in sorted(set(a.functions) & set(b.functions)):
        fa, fb = a.functions[k], b.functions[k]
        if fa["args"] != fb["args"]:
            notes.append(f"~ signature changed: {k}({', '.join(fa['args'])}) -> ({', '.join(fb['args'])})")
        elif fa["fp"] != fb["fp"]:
            notes.append(f"~ body changed: {k}")
    for label, xa, xb in (("module", a.modules, b.modules),
                          ("class", set(a.classes), set(b.classes)),
                          ("import", a.imports, b.imports),
                          ("call", a.calls, b.calls)):
        fmt = (lambda x: f"{x[0]} <- {x[1]}") if label == "import" else \
              (lambda x: f"{x[0]} -> {x[1]}") if label == "call" else str
        notes += [f"- {label}: {fmt(x)}" for x in sorted(xa - xb)]
        notes += [f"+ {label}: {fmt(x)}" for x in sorted(xb - xa)]
    for m in sorted(set(b.errors) - set(a.errors)):
        notes.append(f"! {m}: {b.errors[m]}")
    return notes


# ---------------------------------------------------------------- line interpretations
def _callers_of(name: str, s: Structure) -> list[str]:
    return sorted(c for c, callee in s.calls if callee == name)


def interpret_lines(path: str, text: str, s: Structure | None = None) -> list[str]:
    """One interpretation string per source line: what the line does, where it
    sits in the program, and (for defs) who calls it."""
    lines = text.splitlines()
    out = ["blank" if not l.strip() else "comment" if l.strip().startswith("#") else "continuation"
           for l in lines]
    if not path.endswith(".py"):
        return ["text" if l.strip() else "blank" for l in lines]
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [f"unparseable (syntax error line {e.lineno})" if l.strip() else "blank" for l in lines]
    mod = _module_name(path)

    def describe(n: ast.stmt) -> str:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            d = f"defines function {n.name}({', '.join(a.arg for a in n.args.args)})"
            if s is not None:
                callers = _callers_of(n.name, s)
                d += f"; called by {', '.join(callers)}" if callers else "; not called anywhere"
            return d
        if isinstance(n, ast.ClassDef):
            return f"defines class {n.name}"
        if isinstance(n, ast.Import):
            return "imports " + ", ".join(a.name for a in n.names)
        if isinstance(n, ast.ImportFrom):
            return f"imports {', '.join(a.name for a in n.names)} from {n.module}"
        if isinstance(n, ast.Return):
            return f"returns {ast.unparse(n.value)}" if n.value else "returns None"
        if isinstance(n, ast.AugAssign):
            return f"updates {ast.unparse(n.target)} {type(n.op).__name__.lower()}= {ast.unparse(n.value)}"
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tg = n.targets if isinstance(n, ast.Assign) else [n.target]
            val = ast.unparse(n.value) if n.value is not None else "?"
            return f"sets {', '.join(ast.unparse(t) for t in tg)} = {val}"
        if isinstance(n, (ast.If, ast.While)):
            return f"{type(n).__name__.lower()} {ast.unparse(n.test)}"
        if isinstance(n, ast.For):
            return f"loops {ast.unparse(n.target)} over {ast.unparse(n.iter)}"
        if isinstance(n, ast.Raise):
            return f"raises {ast.unparse(n.exc) if n.exc else ''}".strip()
        if isinstance(n, ast.Expr):
            if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                return "docstring"
            return f"evaluates {ast.unparse(n.value)}"
        return type(n).__name__.lower()

    def walk(body, scope):
        for n in body:
            calls = sorted({_callee(c) for c in ast.walk(n) if isinstance(c, ast.Call) and _callee(c)}) \
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else []
            where = f"in {mod}:{'.'.join(scope)}" if scope else f"in {mod} (top level)"
            d = f"{describe(n)} [{where}]"
            if calls:
                d += f" calls {', '.join(calls)}"
            out[n.lineno - 1] = d
            for sub in ("body", "orelse", "finalbody"):
                inner = getattr(n, sub, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    nscope = scope + [n.name] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
                    walk(inner, nscope)
                    if isinstance(n, (ast.If, ast.While, ast.For)) and sub == "body":
                        for i, inner_n in enumerate(inner):
                            pass
            for h in getattr(n, "handlers", []) or []:
                walk(h.body, scope)
            # compound statements: refine their "calls" to the header only
            if isinstance(n, (ast.If, ast.While, ast.For, ast.With, ast.Try)):
                head = getattr(n, "test", None) or getattr(n, "iter", None)
                hc = sorted({_callee(c) for c in ast.walk(head) if isinstance(c, ast.Call) and _callee(c)}) if head else []
                out[n.lineno - 1] = f"{describe(n)} [{where}]" + (f" calls {', '.join(hc)}" if hc else "")
    walk(tree.body, [])
    return out


# ---------------------------------------------------------------- compare
@dataclass
class Result:
    code_diff: str
    thought_diff: list[str]
    structure_diff: list[str]

    @property
    def empty(self) -> bool:
        return not (self.code_diff or self.thought_diff or self.structure_diff)

    def render(self) -> str:
        parts = ["=== CODE DIFF (working vs frozen original) ===",
                 self.code_diff.rstrip() or "(no changes)",
                 "", "=== THOUGHT DIFF (line interpretations: original -> current) ===",
                 "\n".join(self.thought_diff) or "(no interpretation changes)",
                 "", "=== STRUCTURE DIFF (whole program: original -> current) ===",
                 "\n".join(self.structure_diff) or "(no structure changes)",
                 "", "Note: interpretations are deterministic AST readings of the code as it is now,",
                 "not recovered thoughts of the model that wrote it. Check them against code and tests."]
        return "\n".join(parts) + "\n"


def thought_diff(orig: dict[str, str], work: dict[str, str],
                 sa: Structure, sb: Structure) -> list[str]:
    out = []
    for path in sorted(set(orig) | set(work)):
        a, b = orig.get(path, ""), work.get(path, "")
        ia, ib = interpret_lines(path, a, sa), interpret_lines(path, b, sb)
        la, lb = a.splitlines(), b.splitlines()
        if la == lb and ia == ib:
            continue
        sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                # context lines whose meaning changed because the program around them changed
                for k in range(i2 - i1):
                    if ia[i1 + k] != ib[j1 + k]:
                        out.append(f"{path}:{j1 + k + 1} (unchanged text, reinterpreted)")
                        out.append(f"    - {ia[i1 + k]}")
                        out.append(f"    + {ib[j1 + k]}")
                continue
            for k in range(max(i2 - i1, j2 - j1)):
                oi, nj = i1 + k, j1 + k
                old = ia[oi] if oi < i2 else None
                new = ib[nj] if nj < j2 else None
                if old in ("blank", None) and new in ("blank", None):
                    continue
                loc = f"{path}:{nj + 1}" if new is not None else f"{path}:(was {oi + 1})"
                out.append(f"{loc} ({op})")
                if old is not None:
                    out.append(f"    - {la[oi].strip()!r}: {old}")
                if new is not None:
                    out.append(f"    + {lb[nj].strip()!r}: {new}")
    return out


def compare(original: str | Path | dict, working: str | Path | dict) -> Result:
    orig = original if isinstance(original, dict) else load_tree(original)
    work = working if isinstance(working, dict) else load_tree(working)
    sa, sb = structure_of(orig), structure_of(work)
    return Result(code_diff(orig, work), thought_diff(orig, work, sa, sb), structure_diff(sa, sb))
