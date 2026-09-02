"""Provider routing tests per the 1.2.0 design spec."""

import asyncio

import pytest

from hyperclaw import providers as P


VARS = ("ANTHROPIC_API_KEY", "HYPERSPEED_BASE_URL", "HYPERSPEED_API_KEY",
        "HYPERSPEED_MODEL", "CHATJIMMY_BASE_URL", "CHATJIMMY_API_KEY",
        "CHATJIMMY_MODEL", "OPENAI_API_KEY", "HYPERCLAW_MODEL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    P.reset_registry()
    yield
    P.reset_registry()


def _reg():
    return P.ProviderRegistry.load()


class TestLiveFiltering:
    def test_anthropic_only(self, monkeypatch):
        """With only ANTHROPIC_API_KEY set, every slot resolves to anthropic
        or 'not configured' — no exceptions, no warnings."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        reg = _reg()
        for slot in ("primary", "tools", "vision", "fast"):
            cands = reg.resolve(slot, {"chat"})
            assert cands and all(p.name == "anthropic" for p, _ in cands), slot
        assert reg.resolve("embeddings", {"embeddings"}) == []
        assert reg.resolve("images", {"image_generation"}) == []
        summary = reg.slot_summary()
        assert summary["embeddings"] == "local hash"
        assert summary["images"] == "not configured"

    def test_nothing_configured(self):
        reg = _reg()
        for slot in P.SLOTS:
            assert reg.resolve(slot, {"chat"}) == []

    def test_vaughns_matrix(self, monkeypatch):
        """anthropic + hyperspeed → primary=hyperspeed, tools=anthropic,
        fast=anthropic:fast, images=not configured."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("HYPERSPEED_BASE_URL", "https://example.test/v1")
        monkeypatch.setenv("HYPERSPEED_API_KEY", "hs-test")
        monkeypatch.setenv("HYPERSPEED_MODEL", "vendor/some-model")
        reg = _reg()
        prim = reg.resolve("primary", {"chat", "streaming"})
        assert prim[0][0].name == "hyperspeed" and prim[0][1] == "vendor/some-model"
        assert prim[1][0].name == "anthropic"
        tools = reg.resolve("tools", {"chat", "tool_use"})
        assert [p.name for p, _ in tools] == ["anthropic"]
        fast = reg.resolve("fast", {"chat"})
        assert fast[0][0].name == "anthropic"
        assert "haiku" in fast[0][1]
        assert reg.resolve("images", {"image_generation"}) == []

    def test_partial_provider_not_live(self, monkeypatch):
        """A provider missing any *_env is not live."""
        monkeypatch.setenv("HYPERSPEED_BASE_URL", "https://example.test/v1")
        monkeypatch.setenv("HYPERSPEED_API_KEY", "hs-test")
        # HYPERSPEED_MODEL missing
        reg = _reg()
        assert not reg.providers["hyperspeed"].live
        assert all(p.name != "hyperspeed" for p, _ in reg.resolve("primary", {"chat"}))


class TestCapabilityGating:
    def test_tool_use_never_reaches_openai_compat(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("HYPERSPEED_BASE_URL", "https://example.test/v1")
        monkeypatch.setenv("HYPERSPEED_API_KEY", "hs-test")
        monkeypatch.setenv("HYPERSPEED_MODEL", "vendor/some-model")
        reg = _reg()
        for slot in P.SLOTS:
            for prov, _ in reg.resolve(slot, {"chat", "tool_use"}):
                assert "tool_use" in prov.capabilities

    def test_vision_requires_documents(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("HYPERSPEED_BASE_URL", "https://example.test/v1")
        monkeypatch.setenv("HYPERSPEED_API_KEY", "hs-test")
        monkeypatch.setenv("HYPERSPEED_MODEL", "vendor/some-model")
        reg = _reg()
        vis = reg.resolve("vision", {"chat", "documents"})
        assert [p.name for p, _ in vis] == ["anthropic"]


class TestFailover:
    def _run(self, gen):
        async def collect():
            return [item async for item in gen]
        return asyncio.run(collect())

    def _cands(self):
        a = P.Provider("alpha", "openai_compat", frozenset({"chat"}))
        b = P.Provider("beta", "openai_compat", frozenset({"chat"}))
        return [(a, "m-a"), (b, "m-b")]

    def test_pre_first_token_failover(self):
        calls = []

        async def attempt(prov, model):
            calls.append(prov.name)
            if prov.name == "alpha":
                raise ConnectionError("boom")
            yield ("text", "hello")

        out = self._run(P.stream_with_failover(self._cands(), attempt))
        assert calls == ["alpha", "beta"]
        assert out == [("text", "hello")]
        assert P.get_served_by() == "beta/m-b"

    def test_mid_stream_marked_not_reanswered(self):
        calls = []

        async def attempt(prov, model):
            calls.append(prov.name)
            yield ("text", "partial ")
            raise ConnectionError("died")

        out = self._run(P.stream_with_failover(self._cands(), attempt))
        assert calls == ["alpha"]           # beta must never run
        assert out[0] == ("text", "partial ")
        assert "interrupted" in out[1][1]
        assert P.get_served_by() == "alpha/m-a"

    def test_all_fail(self):
        async def attempt(prov, model):
            raise ConnectionError("nope")
            yield  # pragma: no cover

        out = self._run(P.stream_with_failover(self._cands(), attempt))
        assert len(out) == 1 and "no provider" in out[0][1]


class TestServedBy:
    def test_startup_line_format(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        line = _reg().startup_line()
        assert line.startswith("providers: ")
        assert "anthropic ✓" in line
        assert "hyperspeed –" in line

    def test_identity_prompt_uses_serving_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
        from hyperclaw.solomon import ChatAgent
        agent = ChatAgent.__new__(ChatAgent)
        agent.ai_name, agent.user_name = "TestBot", ""
        prompt = agent._load_system_prompt("vendor/some-model")
        assert "vendor/some-model" in prompt
        assert "Anthropic model" not in prompt
