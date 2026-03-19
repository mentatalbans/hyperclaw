"""
HyperShield FilesystemGuard — controls read/write access per policy.
"""
from __future__ import annotations
import os


class FilesystemGuard:
    def __init__(self, allow_read: list[str] = None, allow_write: list[str] = None,
                 deny: list[str] = None) -> None:
        self._allow_read = allow_read or ["./"]
        self._allow_write = allow_write or ["./"]
        self._deny = deny or []

    def can_read(self, path: str) -> bool:
        return self._check(path, self._allow_read)

    def can_write(self, path: str) -> bool:
        return self._check(path, self._allow_write)

    def _check(self, path: str, allowed: list[str]) -> bool:
        norm = os.path.normpath(path)
        for denied in self._deny:
            if norm.startswith(os.path.normpath(denied)):
                return False
        for a in allowed:
            if norm.startswith(os.path.normpath(a)):
                return True
        return False
