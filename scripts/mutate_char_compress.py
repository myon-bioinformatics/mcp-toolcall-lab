"""Small AST mutation tester for src/mcp_toolcall_lab/char_compress.py.

Each mutant temporarily rewrites the real source file, runs the char_compress
tests, and restores the original (always, via ``finally``). A mutant "survives"
when the tests still pass. Usage: ``uv run --with tiktoken --extra test python
scripts/mutate_char_compress.py``. Exit code 1 when any mutant survives.
"""
from __future__ import annotations

import ast
import os
import copy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/mcp_toolcall_lab/char_compress.py"
TEST = "tests/test_char_compress.py"
SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
        ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
        ast.In: ast.NotIn, ast.NotIn: ast.In}


def _skipped(tree: ast.AST, source: str) -> set[int]:
    """Docstrings and ``# pragma: no cover`` lines are not behaviour."""
    lines = source.splitlines()
    skip = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if "pragma: no cover" in lines[getattr(n, "lineno", 1) - 1]:
            skip.add(id(n))
    return skip


def mutants(tree: ast.AST, source: str):
    """Yield (description, mutated tree)."""
    skip = _skipped(tree, source)
    nodes = list(ast.walk(tree))
    for i, node in enumerate(nodes):
        if id(node) in skip:
            continue
        def variant(fn, desc):
            new = copy.deepcopy(tree)
            target = list(ast.walk(new))[i]
            if fn(target) is not False:
                yield f"L{getattr(node, 'lineno', '?')}: {desc}", new
        if isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                if type(op) in SWAP:
                    yield from variant(lambda t, k=k: t.ops.__setitem__(k, SWAP[type(t.ops[k])]()),
                                       f"{type(op).__name__}->{SWAP[type(op)].__name__}")
        elif isinstance(node, ast.BoolOp):
            other = ast.Or if isinstance(node.op, ast.And) else ast.And
            yield from variant(lambda t, o=other: setattr(t, "op", o()), "and<->or")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield from variant(lambda t: setattr(t, "op", ast.UAdd()), "drop not")
        elif isinstance(node, ast.Constant) and not isinstance(node.value, bool):
            if isinstance(node.value, int):
                yield from variant(lambda t: setattr(t, "value", t.value + 1), "int+1")
            elif isinstance(node.value, str) and node.value:
                yield from variant(lambda t: setattr(t, "value", t.value + "Z"), "str+Z")
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield from variant(lambda t: setattr(t, "value", not t.value), "flip bool")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            other = ast.Sub if isinstance(node.op, ast.Add) else ast.Add
            yield from variant(lambda t, o=other: setattr(t, "op", o()), "+<->-")


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(original)
    killed, survived = 0, []
    try:
        for desc, new in mutants(tree, original):
            code = ast.unparse(new)
            if code == ast.unparse(tree):
                continue
            TARGET.write_text(code, encoding="utf-8")
            # Same-size rewrites within one second would reuse a stale .pyc.
            for pyc in (TARGET.parent / "__pycache__").glob(f"{TARGET.stem}.*.pyc"):
                pyc.unlink()
            proc = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p",
                                   "no:cacheprovider", TEST], cwd=ROOT,
                                  capture_output=True, text=True,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            if proc.returncode == 0:
                survived.append(desc)
            else:
                killed += 1
    finally:
        TARGET.write_text(original, encoding="utf-8")
    total = killed + len(survived)
    print(f"mutants={total} killed={killed} survived={len(survived)}")
    for desc in survived:
        print("SURVIVED", desc)
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
