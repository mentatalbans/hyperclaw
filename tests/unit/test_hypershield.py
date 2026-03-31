"""
Unit tests for security/ — NetworkGuard, FilesystemGuard, AuditLogger, HyperShield.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from security.policy_engine import PolicyEngine
from security.network_guard import NetworkGuard, NetworkEgressBlockedError
from security.filesystem_guard import FilesystemGuard, FilesystemAccessBlockedError
from security.audit_logger import AuditLogger
from security.hypershield import HyperShield


POLICY_YAML = """\
profile: test
network:
  egress_allowlist:
    - "api.anthropic.com"
    - "chatjimmy.ai"
  block_all_other_egress: true
filesystem:
  sandbox_root: "/hyperclaw/sandbox"
  tmp_dir: "/tmp/hyperclaw"
  block_host_reads: true
logging:
  log_all_inference_calls: true
  log_file_access: true
  log_network_requests: true
  output: "hypermemory"
agents:
  ISOLATED_AGENT:
    egress_allowlist: []
    network_mode: "isolated"
"""


@pytest.fixture
def policy_file(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text(POLICY_YAML)
    return str(f)


@pytest.fixture
def policy_engine(policy_file):
    engine = PolicyEngine()
    engine.load(policy_file)
    return engine


def _make_audit_logger():
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return AuditLogger(pool), pool


class TestNetworkGuard:
    def test_allowed_url_returns_true(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = NetworkGuard(policy_engine, audit)
        result = guard.check_egress("agent-1", "https://api.anthropic.com/v1/messages")
        assert result is True

    def test_blocked_url_raises(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = NetworkGuard(policy_engine, audit)
        with pytest.raises(NetworkEgressBlockedError) as exc_info:
            guard.check_egress("agent-1", "https://evil.com/steal")
        assert "evil.com" in str(exc_info.value)
        assert exc_info.value.agent_id == "agent-1"

    def test_isolated_agent_blocked(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = NetworkGuard(policy_engine, audit)
        with pytest.raises(NetworkEgressBlockedError):
            guard.check_egress("ISOLATED_AGENT", "https://api.anthropic.com")

    def test_network_egress_blocked_error_fields(self):
        err = NetworkEgressBlockedError("agent-x", "https://test.com", "default")
        assert err.agent_id == "agent-x"
        assert err.url == "https://test.com"
        assert err.policy == "default"
        assert isinstance(err, Exception)


class TestFilesystemGuard:
    def test_sandbox_path_allowed(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = FilesystemGuard(policy_engine, audit)
        result = guard.validate_read("agent-1", "/hyperclaw/sandbox/output.txt")
        assert result is True

    def test_tmp_path_allowed(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = FilesystemGuard(policy_engine, audit)
        result = guard.validate_write("agent-1", "/tmp/hyperclaw/scratch.txt")
        assert result is True

    def test_host_path_raises(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = FilesystemGuard(policy_engine, audit)
        with pytest.raises(FilesystemAccessBlockedError) as exc_info:
            guard.validate_read("agent-1", "/etc/passwd")
        assert exc_info.value.agent_id == "agent-1"
        assert "/etc/passwd" in exc_info.value.path

    def test_home_path_raises(self, policy_engine):
        audit, _ = _make_audit_logger()
        guard = FilesystemGuard(policy_engine, audit)
        with pytest.raises(FilesystemAccessBlockedError):
            guard.validate_read("agent-1", "/Users/admin/.ssh/id_rsa")

    def test_filesystem_access_blocked_error_fields(self):
        err = FilesystemAccessBlockedError("agent-x", "/etc/passwd", "/sandbox")
        assert err.agent_id == "agent-x"
        assert err.path == "/etc/passwd"
        assert err.allowed_root == "/sandbox"
        assert isinstance(err, Exception)


class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_writes_to_db(self):
        audit, pool = _make_audit_logger()
        await audit.log(
            event_type="inference_call",
            action="inference",
            allowed=True,
            agent_id="test-agent",
            model_used="claude-sonnet-4-6",
        )
        pool.acquire.return_value.__aenter__.return_value.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_correct_fields(self):
        audit, pool = _make_audit_logger()
        await audit.log(
            event_type="network_request",
            action="egress",
            allowed=False,
            agent_id="agent-1",
            target="https://evil.com",
            policy_applied="default",
        )
        conn = pool.acquire.return_value.__aenter__.return_value
        call_args = conn.execute.call_args[0]
        assert "INSERT INTO audit_log" in call_args[0]
        # Verify key values passed
        assert "network_request" in call_args
        assert "agent-1" in call_args
        assert False in call_args

    @pytest.mark.asyncio
    async def test_get_blocked_events_queries_db(self):
        audit, pool = _make_audit_logger()
        await audit.get_blocked_events(since_hours=24)
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch.assert_called_once()
        sql = conn.fetch.call_args[0][0]
        assert "allowed = false" in sql

    @pytest.mark.asyncio
    async def test_get_recent_queries_db(self):
        audit, pool = _make_audit_logger()
        await audit.get_recent(limit=10)
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_agent_activity(self):
        audit, pool = _make_audit_logger()
        await audit.get_agent_activity("agent-1")
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch.assert_called_once()


class TestHyperShield:
    def _make_pool(self):
        pool = MagicMock()
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=1)
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        return pool

    @pytest.mark.asyncio
    async def test_initialize_loads_policy(self, policy_file):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)
        await shield.initialize()
        assert shield.policy_engine.active.profile == "test"

    @pytest.mark.asyncio
    async def test_initialize_no_exception_on_valid_config(self, policy_file):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)
        # Should not raise
        await shield.initialize()

    @pytest.mark.asyncio
    async def test_initialize_handles_missing_policy_gracefully(self, tmp_path):
        pool = self._make_pool()
        shield = HyperShield("/nonexistent/policy.yaml", pool)
        # Should not raise — uses defaults
        await shield.initialize()

    def test_reload_policy_updates(self, policy_file, tmp_path):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)

        new_policy_file = tmp_path / "new.yaml"
        new_policy_file.write_text("profile: reloaded\nnetwork:\n  egress_allowlist: []\n")
        shield.reload_policy(str(new_policy_file))
        assert shield.policy_engine.active.profile == "reloaded"

    def test_get_network_guard(self, policy_file):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)
        assert isinstance(shield.get_network_guard(), NetworkGuard)

    def test_get_filesystem_guard(self, policy_file):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)
        assert isinstance(shield.get_filesystem_guard(), FilesystemGuard)

    def test_get_audit_logger(self, policy_file):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)
        assert isinstance(shield.get_audit_logger(), AuditLogger)

    @pytest.mark.asyncio
    async def test_check_inference_call_returns_true(self, policy_file):
        pool = self._make_pool()
        shield = HyperShield(policy_file, pool)
        result = await shield.check_inference_call("agent-1", "claude-sonnet-4-6")
        assert result is True
