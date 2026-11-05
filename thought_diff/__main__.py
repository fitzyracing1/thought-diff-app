import argparse
import json
import sys
from . import __version__
from .core import compare, load_tree, structure_of, structure_lines


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="thought_diff", description=(
        "Compare a working copy against a frozen copy of AI-written code: code diff, "
        "line-interpretation diff, and program-structure diff."))
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="original/ (frozen) vs working/")
    c.add_argument("original")
    c.add_argument("working")
    c.add_argument("--exit-code", action="store_true", help="exit 1 if anything differs")
    c.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of text")
    st = sub.add_parser("structure", help="print the structure view of one directory")
    st.add_argument("path")
    a = p.parse_args(argv)
    if a.cmd == "compare":
        r = compare(a.original, a.working)
        if a.json:
            payload = {
                "empty": r.empty,
                "code_diff": r.code_diff,
                "thought_diff": r.thought_diff,
                "structure_diff": r.structure_diff,
                "disclaimer": (
                    "Interpretations are deterministic AST readings of the code as it is now, "
                    "not recovered thoughts of the model that wrote it. Not regeneration."
                ),
            }
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        else:
            sys.stdout.write(r.render())
        return 1 if (a.exit_code and not r.empty) else 0
    print("\n".join(structure_lines(structure_of(load_tree(a.path)))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
