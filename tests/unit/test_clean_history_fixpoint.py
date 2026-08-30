"""Regression tests for clean_history fixpoint repair.

The production failure (2026-08-28): a trimmed window starting with an
assistant tool_use message passed single-sweep validation, then the
leading-assistant pop orphaned the tool_result at messages.0 — the API
rejected it with `unexpected tool_use_id`. Repair must iterate until
stable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _load_clean_history():
    src = (Path(__file__).resolve().parents[2] / "hyperclaw" / "tui.py").read_text()
    start = src.index("def clean_history")
    end = src.index("\ndef chat(message):")
    ns = {}
    exec(src[start:end], ns)
    return ns["clean_history"]


def tr(i):
    return {"type": "tool_result", "tool_use_id": i, "content": "ok"}


def tu(i):
    return {"type": "tool_use", "id": i, "name": "bash", "input": {}}


def txt(t):
    return {"type": "text", "text": t}


def _no_orphans(msgs):
    """Assert the exact API contract the 400s were violating."""
    for i, m in enumerate(msgs):
        if m["role"] == "user" and isinstance(m["content"], list):
            prev = msgs[i - 1] if i > 0 else None
            valid = set()
            if prev and prev["role"] == "assistant" and isinstance(prev["content"], list):
                valid = {b["id"] for b in prev["content"]
                         if isinstance(b, dict) and b.get("type") == "tool_use"}
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    assert b["tool_use_id"] in valid, f"orphan at messages.{i}"
    if msgs:
        assert msgs[0]["role"] == "user"


class TestCleanHistoryFixpoint:
    def test_window_starting_with_assistant_tool_use(self):
        """The exact production 400: leading assistant pop orphans the result."""
        ch = _load_clean_history()
        h = [
            {"role": "assistant", "content": [txt("running"), tu("X")]},
            {"role": "user", "content": [tr("X")]},
            {"role": "assistant", "content": [txt("done")]},
            {"role": "user", "content": "next question"},
        ]
        out = ch(h)
        _no_orphans(out)
        assert out[-1]["content"] == "next question"

    def test_chain_of_leading_tool_exchanges(self):
        ch = _load_clean_history()
        h = [
            {"role": "assistant", "content": [tu("A")]},
            {"role": "user", "content": [tr("A")]},
            {"role": "assistant", "content": [tu("B")]},
            {"role": "user", "content": [tr("B"), txt("and my question")]},
        ]
        out = ch(h)
        _no_orphans(out)
        # the user's actual text must survive the repair
        flat = "".join(b.get("text", "") for m in out
                       for b in (m["content"] if isinstance(m["content"], list) else [])
                       if isinstance(b, dict))
        joined = flat + "".join(m["content"] for m in out if isinstance(m["content"], str))
        assert "and my question" in joined

    def test_merge_does_not_break_adjacency(self):
        ch = _load_clean_history()
        h = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [tu("Q")]},   # unanswered -> emptied
            {"role": "user", "content": [tr("Q"), txt("second")]},
        ]
        out = ch(h)
        _no_orphans(out)

    def test_healthy_history_untouched(self):
        ch = _load_clean_history()
        h = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [txt("hi"), tu("K")]},
            {"role": "user", "content": [tr("K")]},
            {"role": "assistant", "content": [txt("done")]},
        ]
        out = ch(h)
        assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant"]
        _no_orphans(out)

    def test_pathological_soup_converges(self):
        ch = _load_clean_history()
        h = [
            {"role": "assistant", "content": [tu("1")]},
            {"role": "user", "content": [tr("1")]},
            {"role": "assistant", "content": [tu("2")]},
            {"role": "user", "content": [tr("2")]},
            {"role": "assistant", "content": [tu("3")]},
            {"role": "user", "content": [tr("9"), tr("3")]},
            {"role": "assistant", "content": [txt("mid")]},
            {"role": "user", "content": [tr("ghost")]},
            {"role": "user", "content": "real question"},
        ]
        out = ch(h)
        _no_orphans(out)
        # idempotent: running again changes nothing
        assert ch(out) == out
