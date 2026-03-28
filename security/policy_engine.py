"""
HyperShield PolicyEngine — loads and evaluates security policies from YAML.
Supports hot-reload without restart.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import yaml

log = logging.getLogger("hyperclaw.policy_engine")


@dataclass
class NetworkPolicy:
    egress_allowlist: list[str] = field(default_factory=list)
    block_all_other_egress: bool = True


@dataclass
class FilesystemPolicy:
    sandbox_root: str = "/hyperclaw/sandbox"
    tmp_dir: str = "/tmp/hyperclaw"
    block_host_reads: bool = True


@dataclass
class LoggingPolicy:
    log_all_inference_calls: bool = True
    log_file_access: bool = True
    log_network_requests: bool = True
    output: str = "hypermemory"  # 'hypermemory' | 'file' | 'stdout'


@dataclass
class AgentPolicy:
    egress_allowlist: list[str] = field(default_factory=list)
    require_explicit_consent: bool = False
    strip_pii_from_logs: bool = False
    network_mode: str = "standard"  # 'standard' | 'read_only' | 'isolated'


@dataclass
class Policy:
    profile: str
    network: NetworkPolicy
    filesystem: FilesystemPolicy
    logging: LoggingPolicy
    agents: dict[str, AgentPolicy] = field(default_factory=dict)


class PolicyReloadedEvent:
    def __init__(self, old_profile: str, new_profile: str) -> None:
        self.old_profile = old_profile
        self.new_profile = new_profile


class PolicyEngine:
    """
    Loads, parses, and evaluates HyperShield policies from YAML files.
    Supports hot-reload via reload().
    """

    _DEFAULT_POLICY = Policy(
        profile="default",
        network=NetworkPolicy(
            egress_allowlist=["api.anthropic.com", "chatjimmy.ai"],
            block_all_other_egress=True,
        ),
        filesystem=FilesystemPolicy(),
        logging=LoggingPolicy(),
    )

    def __init__(self) -> None:
        self._active: Policy = self._DEFAULT_POLICY
        self._policy_path: str | None = None

    def load(self, policy_path: str) -> Policy:
        """Parse YAML policy file into Policy dataclass."""
        with open(policy_path) as f:
            raw = yaml.safe_load(f) or {}
        policy = self._parse(raw)
        self._active = policy
        self._policy_path = policy_path
        log.info(f"PolicyEngine: loaded profile='{policy.profile}' from {policy_path}")
        return policy

    def reload(self, policy_path: str) -> Policy:
        """Hot-reload policy without restart. Emits PolicyReloadedEvent."""
        old_profile = self._active.profile
        policy = self.load(policy_path)
        event = PolicyReloadedEvent(old_profile, policy.profile)
        log.info(
            f"PolicyEngine: hot-reloaded {old_profile!r} → {policy.profile!r}"
        )
        return policy

    @property
    def active(self) -> Policy:
        return self._active

    def get_agent_policy(self, agent_id: str) -> AgentPolicy:
        """Return agent-specific policy, or default AgentPolicy if not specified."""
        return self._active.agents.get(agent_id, AgentPolicy())

    def is_egress_allowed(self, agent_id: str, destination: str) -> bool:
        """
        Check if an agent may connect to destination.
        Extracts domain from full URL before checking.
        First checks agent-specific policy, falls back to global network policy.
        """
        domain = _extract_domain(destination)
        agent_policy = self.get_agent_policy(agent_id)

        # Isolated agents: no egress ever
        if agent_policy.network_mode == "isolated":
            return False

        # Agent-level allowlist takes precedence
        if agent_policy.egress_allowlist:
            return any(_domain_matches(domain, allowed) for allowed in agent_policy.egress_allowlist)

        # Global network policy
        net = self._active.network
        if any(_domain_matches(domain, allowed) for allowed in net.egress_allowlist):
            return True
        return not net.block_all_other_egress

    def validate_filesystem_access(self, agent_id: str, path: str) -> bool:
        """
        Return True if path is within sandbox_root or tmp_dir.
        False if block_host_reads is True and path escapes sandbox.
        """
        import os
        fs = self._active.filesystem
        norm = os.path.normpath(path)
        allowed_roots = [
            os.path.normpath(fs.sandbox_root),
            os.path.normpath(fs.tmp_dir),
        ]
        return any(norm.startswith(root) for root in allowed_roots)

    @staticmethod
    def _parse(raw: dict) -> Policy:
        net_raw = raw.get("network", {})
        fs_raw = raw.get("filesystem", {})
        log_raw = raw.get("logging", {})
        agents_raw = raw.get("agents", {})

        network = NetworkPolicy(
            egress_allowlist=net_raw.get("egress_allowlist", []),
            block_all_other_egress=net_raw.get("block_all_other_egress", True),
        )
        filesystem = FilesystemPolicy(
            sandbox_root=fs_raw.get("sandbox_root", "/hyperclaw/sandbox"),
            tmp_dir=fs_raw.get("tmp_dir", "/tmp/hyperclaw"),
            block_host_reads=fs_raw.get("block_host_reads", True),
        )
        logging_policy = LoggingPolicy(
            log_all_inference_calls=log_raw.get("log_all_inference_calls", True),
            log_file_access=log_raw.get("log_file_access", True),
            log_network_requests=log_raw.get("log_network_requests", True),
            output=log_raw.get("output", "hypermemory"),
        )
        agents: dict[str, AgentPolicy] = {}
        for agent_id, ap_raw in (agents_raw or {}).items():
            if ap_raw is None:
                ap_raw = {}
            agents[agent_id] = AgentPolicy(
                egress_allowlist=ap_raw.get("egress_allowlist", []),
                require_explicit_consent=ap_raw.get("require_explicit_consent", False),
                strip_pii_from_logs=ap_raw.get("strip_pii_from_logs", False),
                network_mode=ap_raw.get("network_mode", "standard"),
            )

        return Policy(
            profile=raw.get("profile", "default"),
            network=network,
            filesystem=filesystem,
            logging=logging_policy,
            agents=agents,
        )


def _extract_domain(url: str) -> str:
    """Extract domain from a URL or return the string as-is if not a URL."""
    match = re.match(r"https?://([^/:]+)", url)
    if match:
        return match.group(1)
    # Already a domain
    return url.split("/")[0]


def _domain_matches(domain: str, pattern: str) -> bool:
    """Check if domain matches pattern (exact or suffix match)."""
    return domain == pattern or domain.endswith("." + pattern)
