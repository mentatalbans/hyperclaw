"""
HyperShield NetworkGuard — filters outbound network requests per policy.
"""
from __future__ import annotations
import re


class NetworkGuard:
    def __init__(self, allowed_domains: list[str] = None, deny_domains: list[str] = None) -> None:
        self._allowed = allowed_domains or ["*"]
        self._denied = deny_domains or []

    def is_allowed(self, url: str) -> bool:
        domain = self._extract_domain(url)
        for denied in self._denied:
            if denied != "*" and domain.endswith(denied):
                return False
        if "*" in self._allowed:
            return True
        return any(domain.endswith(a) for a in self._allowed)

    @staticmethod
    def _extract_domain(url: str) -> str:
        match = re.match(r"https?://([^/]+)", url)
        return match.group(1) if match else url
