"""Tests for hyperclaw.api_utils — thinking-block-safe response parsing
and config file discovery."""

import json
from types import SimpleNamespace

import pytest

from hyperclaw.api_utils import extract_text, extract_json, find_config


def _block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks))


class TestExtractText:
    def test_thinking_block_first(self):
        resp = _resp(
            _block("thinking", thinking="pondering..."),
            _block("text", text="the answer"),
        )
        assert extract_text(resp) == "the answer"

    def test_thinking_only_returns_default(self):
        resp = _resp(_block("thinking", thinking="pondering..."))
        assert extract_text(resp) == ""
        assert extract_text(resp, default="fallback") == "fallback"

    def test_multiple_text_blocks_joined(self):
        resp = _resp(_block("text", text="one"), _block("text", text="two"))
        assert extract_text(resp) == "one\ntwo"

    def test_tool_use_blocks_skipped(self):
        resp = _resp(
            _block("text", text="calling a tool"),
            _block("tool_use", id="toolu_1", name="t", input={}),
        )
        assert extract_text(resp) == "calling a tool"

    def test_empty_and_none_content(self):
        assert extract_text(_resp()) == ""
        assert extract_text(SimpleNamespace(content=None), default="d") == "d"

    def test_empty_text_block_skipped(self):
        # display: omitted can leave empty-string text fields around
        resp = _resp(_block("text", text=""), _block("text", text="real"))
        assert extract_text(resp) == "real"


class TestExtractJson:
    def test_plain_json(self):
        resp = _resp(_block("text", text='{"a": 1}'))
        assert extract_json(resp) == {"a": 1}

    def test_fenced_json_after_thinking(self):
        resp = _resp(
            _block("thinking", thinking="hmm"),
            _block("text", text='```json\n{"a": [1, 2]}\n```'),
        )
        assert extract_json(resp) == {"a": [1, 2]}

    def test_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            extract_json(_resp(_block("text", text="not json")))


class TestFindConfig:
    def test_seeds_user_copy_from_packaged_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
        p = find_config("agents.yaml")
        assert p == tmp_path / "config" / "agents.yaml"
        assert p.exists()

    def test_prefers_existing_user_copy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
        user = tmp_path / "config" / "agents.yaml"
        user.parent.mkdir(parents=True)
        user.write_text("agents: []\n")
        assert find_config("agents.yaml") == user
        assert user.read_text() == "agents: []\n"

    def test_missing_name_returns_user_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
        p = find_config("nope.yaml")
        assert not p.exists()
        assert p == tmp_path / "config" / "nope.yaml"
