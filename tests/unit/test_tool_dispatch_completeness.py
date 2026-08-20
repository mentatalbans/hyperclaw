"""Dispatch-table completeness: every advertised tool must resolve to a handler.

Parses hyperclaw/tui.py with ast instead of importing it (module import has side
effects: loads memories, spawns clients). Guards against the classic
crash-on-specific-tool pattern where a schema exists but no dispatch branch does.
"""
import ast
from pathlib import Path

TUI_PATH = Path(__file__).resolve().parents[2] / "hyperclaw" / "tui.py"


def _module():
    return ast.parse(TUI_PATH.read_text())


def _advertised_names(tree):
    """String values of "name" keys in dicts inside the first TOOLS = [...] literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "TOOLS" and isinstance(node.value, ast.List):
                    names = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Dict):
                            for k, v in zip(elt.keys, elt.values):
                                if (isinstance(k, ast.Constant) and k.value == "name"
                                        and isinstance(v, ast.Constant)):
                                    names.append(v.value)
                    return names
    raise AssertionError("TOOLS list literal not found in tui.py")


def _stub_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_STUB_TOOLS" and isinstance(node.value, ast.Set):
                    return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return set()


def _dispatch_info(tree):
    """(exact_names, prefixes) handled inside execute_tool."""
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "execute_tool")
    exact, prefixes = set(), set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "name":
            for comp in node.comparators:
                if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    exact.add(comp.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "name"
                and node.args and isinstance(node.args[0], ast.Constant)):
            prefixes.add(node.args[0].value)
    return exact, prefixes


def test_every_advertised_tool_has_a_dispatch_branch():
    tree = _module()
    advertised = set(_advertised_names(tree)) - _stub_names(tree)
    exact, prefixes = _dispatch_info(tree)
    unhandled = {n for n in advertised
                 if n not in exact and not any(n.startswith(p) for p in prefixes)}
    assert not unhandled, f"Advertised tools with no dispatch branch: {sorted(unhandled)}"


def test_stub_tools_are_not_advertised():
    tree = _module()
    stubs = _stub_names(tree)
    if not stubs:
        import pytest
        pytest.skip("no _STUB_TOOLS filter in this distribution (no stub tools shipped)")
    # The runtime filter is TOOLS = [t for t in TOOLS if t["name"] not in _STUB_TOOLS];
    # verify the filter expression exists so the raw list isn't re-exposed by accident.
    src = TUI_PATH.read_text()
    assert 'not in _STUB_TOOLS' in src


def test_no_duplicate_tool_names():
    names = _advertised_names(_module())
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"Duplicate tool schemas: {sorted(dupes)}"
