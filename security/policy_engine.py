"""
HyperShield PolicyEngine — loads and evaluates security policies from YAML.
"""
from __future__ import annotations
import os
import logging
import yaml

log = logging.getLogger("hyperclaw.policy_engine")

_POLICY_DIR = os.path.join(os.path.dirname(__file__), "policies")


class PolicyEngine:
    def __init__(self, policy_name: str = "default") -> None:
        self._policy_name = policy_name
        self._policy: dict = self._load(policy_name)

    def _load(self, name: str) -> dict:
        path = os.path.join(_POLICY_DIR, f"{name}.yaml")
        if not os.path.exists(path):
            log.warning(f"Policy '{name}' not found at {path}, using empty policy")
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}

    def is_allowed(self, action: str, resource: str) -> bool:
        """Check if action on resource is allowed under current policy."""
        # Filesystem checks
        if action in ("read", "write"):
            deny_list = self._policy.get("filesystem", {}).get("deny", [])
            for denied in deny_list:
                if resource.startswith(denied):
                    return False
        return True

    def get(self, key: str, default=None):
        return self._policy.get(key, default)
