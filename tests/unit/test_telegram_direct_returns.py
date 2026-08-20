"""telegram_direct send_photo/send_file must return a dict on EVERY path.

The 2026-08-20 half-applied patch left send_photo's except block returning None,
which crashed callers doing result.get(...). Verified structurally (ast) so the
test needs no Telegram credentials and never touches the network.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "scripts" / "telegram_direct.py"


def _method(name):
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _handlers_return_value(fn):
    """Every except handler in fn must contain at least one `return <expr>` (not bare)."""
    problems = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
            if not returns or any(r.value is None for r in returns):
                problems.append(ast.dump(node)[:80])
    return problems


def test_send_photo_except_returns_dict():
    assert not _handlers_return_value(_method("send_photo"))


def test_send_file_except_returns_dict():
    assert not _handlers_return_value(_method("send_file"))


def test_send_file_no_unreachable_double_return():
    fn = _method("send_file")
    src_lines = SRC.read_text().splitlines()
    body = "\n".join(src_lines[fn.lineno - 1: fn.end_lineno])
    assert body.count("return {}\n            return {}") == 0, "duplicated unreachable return"
