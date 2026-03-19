"""
Unit tests for security/policy_engine.py — PolicyEngine load, egress, filesystem checks.
"""
from __future__ import annotations

import os
import tempfile
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from security.policy_engine import PolicyEngine, Policy, AgentPolicy, NetworkPolicy


SAMPLE_POLICY_YAML = """\
profile: test_policy
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
  log_file_access: false
  log_network_requests: true
  output: "hypermemory"
agents:
  SCOUT:
    egress_allowlist:
      - "arxiv.org"
      - "api.github.com"
    network_mode: "read_only"
  VITALS:
    egress_allowlist: []
    require_explicit_consent: true
    network_mode: "isolated"
"""


@pytest.fixture
def policy_file(tmp_path):
    f = tmp_path / "test_policy.yaml"
    f.write_text(SAMPLE_POLICY_YAML)
    return str(f)


class TestPolicyEngineLoad:
    def test_loads_profile(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        assert policy.profile == "test_policy"

    def test_loads_network_allowlist(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        assert "api.anthropic.com" in policy.network.egress_allowlist
        assert "chatjimmy.ai" in policy.network.egress_allowlist

    def test_loads_block_all_other_egress(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        assert policy.network.block_all_other_egress is True

    def test_loads_filesystem_policy(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        assert policy.filesystem.sandbox_root == "/hyperclaw/sandbox"
        assert policy.filesystem.tmp_dir == "/tmp/hyperclaw"

    def test_loads_agent_policies(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        assert "SCOUT" in policy.agents
        assert "VITALS" in policy.agents

    def test_agent_scout_allowlist(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        scout = policy.agents["SCOUT"]
        assert "arxiv.org" in scout.egress_allowlist
        assert scout.network_mode == "read_only"

    def test_agent_vitals_isolated(self, policy_file):
        engine = PolicyEngine()
        policy = engine.load(policy_file)
        vitals = policy.agents["VITALS"]
        assert vitals.network_mode == "isolated"
        assert vitals.require_explicit_consent is True

    def test_raises_on_missing_file(self):
        engine = PolicyEngine()
        with pytest.raises(FileNotFoundError):
            engine.load("/nonexistent/policy.yaml")

    def test_reload_updates_active_policy(self, policy_file, tmp_path):
        engine = PolicyEngine()
        engine.load(policy_file)
        assert engine.active.profile == "test_policy"

        # Write a different policy
        new_policy = tmp_path / "new_policy.yaml"
        new_policy.write_text("profile: new_profile\nnetwork:\n  egress_allowlist: []\n")
        engine.reload(str(new_policy))
        assert engine.active.profile == "new_profile"


class TestPolicyEngineEgress:
    @pytest.fixture
    def engine(self, policy_file):
        e = PolicyEngine()
        e.load(policy_file)
        return e

    def test_allows_listed_domain(self, engine):
        assert engine.is_egress_allowed("default_agent", "https://api.anthropic.com/v1/messages") is True

    def test_allows_listed_domain_direct(self, engine):
        assert engine.is_egress_allowed("default_agent", "https://chatjimmy.ai/api") is True

    def test_blocks_unlisted_domain(self, engine):
        assert engine.is_egress_allowed("default_agent", "https://evil.com/steal") is False

    def test_isolated_agent_always_blocked(self, engine):
        assert engine.is_egress_allowed("VITALS", "https://api.anthropic.com") is False

    def test_scout_allows_arxiv(self, engine):
        assert engine.is_egress_allowed("SCOUT", "https://arxiv.org/abs/1234") is True

    def test_scout_blocks_other(self, engine):
        assert engine.is_egress_allowed("SCOUT", "https://evil.com") is False

    def test_unknown_agent_uses_global_policy(self, engine):
        # Unknown agent has no specific policy — uses global allowlist
        assert engine.is_egress_allowed("UNKNOWN_AGENT", "https://api.anthropic.com") is True
        assert engine.is_egress_allowed("UNKNOWN_AGENT", "https://evil.com") is False

    def test_get_agent_policy_returns_default_for_unknown(self, engine):
        ap = engine.get_agent_policy("NOBODY")
        assert isinstance(ap, AgentPolicy)
        assert ap.network_mode == "standard"


class TestPolicyEngineFilesystem:
    @pytest.fixture
    def engine(self, policy_file):
        e = PolicyEngine()
        e.load(policy_file)
        return e

    def test_sandbox_path_allowed(self, engine):
        assert engine.validate_filesystem_access("agent", "/hyperclaw/sandbox/output.txt") is True

    def test_tmp_path_allowed(self, engine):
        assert engine.validate_filesystem_access("agent", "/tmp/hyperclaw/scratch.txt") is True

    def test_host_path_blocked(self, engine):
        assert engine.validate_filesystem_access("agent", "/etc/passwd") is False

    def test_home_path_blocked(self, engine):
        assert engine.validate_filesystem_access("agent", "/Users/admin/.ssh/id_rsa") is False

    def test_nested_sandbox_path_allowed(self, engine):
        assert engine.validate_filesystem_access(
            "agent", "/hyperclaw/sandbox/agents/output/result.json"
        ) is True
