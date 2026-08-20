"""Failover-chain unit tests against real anthropic exception types.

TUIBridge is constructed via __new__ to skip __init__ (which imports the full
tui module); _create_with_failover and _failover_chain only touch self._FAILOVER_CHAIN.
"""
import time
import types

import anthropic
import httpx
import pytest

from hyperclaw.tui_bridge import TUIBridge


def _status_error(code):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(code, request=req, json={"error": {"message": "x"}})
    return anthropic.APIStatusError(f"http {code}", response=resp, body=None)


class FakeMessages:
    def __init__(self, script):
        # script: model -> exception instance OR response object
        self.script = script
        self.calls = []

    def create(self, *, model, **kwargs):
        self.calls.append(model)
        action = self.script[model]
        if isinstance(action, Exception):
            raise action
        return action


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def _bridge():
    return TUIBridge.__new__(TUIBridge)


def _resp(stop_reason="end_turn", stop_details=None):
    return types.SimpleNamespace(stop_reason=stop_reason, stop_details=stop_details, content=[])


def test_chain_honors_env_overrides(monkeypatch):
    monkeypatch.setenv("FABLE_MODEL", "custom-fable")
    monkeypatch.setenv("HYPERCLAW_OPUS_MODEL", "custom-opus")
    monkeypatch.setenv("HYPERCLAW_SONNET_MODEL", "custom-sonnet")
    chain = _bridge()._failover_chain()
    assert chain == ["custom-fable", "custom-opus", "custom-sonnet"]


def test_400_raises_immediately():
    b = _bridge()
    client = FakeClient({"claude-fable-5": _status_error(400)})
    with pytest.raises(anthropic.APIStatusError):
        b._create_with_failover(client, model="claude-fable-5")
    assert client.messages.calls == ["claude-fable-5"]


def test_529_walks_the_ladder(monkeypatch):
    monkeypatch.delenv("FABLE_MODEL", raising=False)
    monkeypatch.delenv("HYPERCLAW_OPUS_MODEL", raising=False)
    monkeypatch.delenv("HYPERCLAW_SONNET_MODEL", raising=False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    ok = _resp()
    b = _bridge()
    client = FakeClient({
        "claude-fable-5": _status_error(529),
        "claude-opus-5": _status_error(429),
        "claude-sonnet-5": ok,
    })
    resp, used = b._create_with_failover(client, model="claude-fable-5")
    assert resp is ok
    assert used == "claude-sonnet-5"
    assert client.messages.calls == ["claude-fable-5", "claude-opus-5", "claude-sonnet-5"]


def test_all_rungs_fail_raises_last_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    err = _status_error(529)
    b = _bridge()
    client = FakeClient({
        "claude-fable-5": err,
        "claude-opus-5": err,
        "claude-sonnet-5": err,
    })
    with pytest.raises(anthropic.APIStatusError):
        b._create_with_failover(client, model="claude-fable-5")


def test_refusal_steps_down_and_surfaces(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    refusal = _resp(stop_reason="refusal",
                    stop_details=types.SimpleNamespace(category="cyber", explanation="no"))
    ok = _resp()
    b = _bridge()
    client = FakeClient({
        "claude-fable-5": refusal,
        "claude-opus-5": ok,
        "claude-sonnet-5": ok,
    })
    resp, used = b._create_with_failover(client, model="claude-fable-5")
    assert resp is ok
    assert used == "claude-opus-5"


def test_whole_chain_refuses_returns_refusal(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    refusal = _resp(stop_reason="refusal", stop_details=None)
    b = _bridge()
    client = FakeClient({
        "claude-fable-5": refusal,
        "claude-opus-5": refusal,
        "claude-sonnet-5": refusal,
    })
    resp, used = b._create_with_failover(client, model="claude-fable-5")
    assert resp.stop_reason == "refusal"


def test_deadline_stops_ladder(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = _bridge()
    client = FakeClient({
        "claude-fable-5": _status_error(529),
        "claude-opus-5": _resp(),
        "claude-sonnet-5": _resp(),
    })
    # Deadline already passed: rung 0 runs (attempt 0 is always tried), later rungs skipped
    with pytest.raises(anthropic.APIStatusError):
        b._create_with_failover(client, model="claude-fable-5",
                                deadline=time.monotonic() - 1)
    assert client.messages.calls == ["claude-fable-5"]


def test_connection_error_walks_ladder(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    ok = _resp()
    b = _bridge()
    client = FakeClient({
        "claude-fable-5": anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
        "claude-opus-5": ok,
        "claude-sonnet-5": ok,
    })
    resp, used = b._create_with_failover(client, model="claude-fable-5")
    assert used == "claude-opus-5"
