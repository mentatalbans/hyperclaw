"""Explicit read-only configuration and opt-in v2 root initialization."""
from collections.abc import Mapping
import json
import os
from pathlib import Path
import secrets
import re
from typing import Literal
import tomllib
from urllib.parse import urlsplit, urlunsplit

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from hyperclaw.contracts import InvalidRequest, Value


class Settings(Value):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True, allow_inf_nan=False)
    root: Path
    ollama_url: str = 'http://127.0.0.1:11434'
    model: str = 'qwen3.8:27b-mlx'
    workspace_path: str = ''
    mcp_docs_path: str = ''
    mcp_docs_image: str = ''
    mcp_protocol: Literal['2026-07-28', '2025-11-25'] = '2026-07-28'
    thinking: bool = False
    max_output_tokens: int = Field(default=4096, gt=0)
    request_timeout_s: float = Field(default=120, gt=0)
    run_timeout_s: float = Field(default=600, gt=0)
    port: int = Field(default=8011, ge=0, le=65535)

    @model_validator(mode='after')
    def valid_mcp(self):
        if bool(self.mcp_docs_path) != bool(self.mcp_docs_image):
            raise ValueError('MCP requires both documentation path and immutable image')
        if self.mcp_docs_path and (not Path(self.mcp_docs_path).is_absolute() or '\0' in self.mcp_docs_path):
            raise ValueError('MCP documentation path must be absolute')
        if self.mcp_docs_image and re.fullmatch(r'(?:sha256:|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:)[0-9a-f]{64}', self.mcp_docs_image) is None:
            raise ValueError('MCP image must be a resolved sha256 ID or repository digest')
        return self

    @field_validator('model')
    @classmethod
    def valid_model(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid model')
        return value

    @field_validator('ollama_url')
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
            raise ValueError('Invalid endpoint')
        _ = parsed.port
        path = parsed.path.rstrip('/')
        if path.endswith('/v1'):
            path = path[:-3]
        return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def load_settings(root: Path | None = None, overrides: dict | None = None,
                  environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    selected = Path(root if root is not None else env.get('HYPERCLAW_ROOT', '~/.hyperclaw-v2')).expanduser().absolute()
    try:
        if selected.is_symlink() or (selected / 'config.toml').is_symlink():
            raise InvalidRequest('unsafe_root', 'Runtime root/configuration must not be symlinks.')
        config = selected / 'config.toml'
        values = tomllib.loads(config.read_text()) if config.exists() else {}
        if 'root' in values or 'root' in (overrides or {}):
            raise InvalidRequest('invalid_config', 'Select the runtime root with --root or HYPERCLAW_ROOT.')
        # Validate file independently so overrides cannot hide malformed configuration.
        Settings(root=selected, **values)
        return Settings(root=selected, **(values | (overrides or {})))
    except (ValueError, OSError, ValidationError, TypeError):
        raise InvalidRequest('invalid_config', 'Invalid config.toml or explicit settings.') from None


def ensure_root(root: Path) -> None:
    """Adopt only an empty root. Never reinterpret existing personal data."""
    try:
        if root.is_symlink():
            raise InvalidRequest('unsafe_root', 'Runtime root must not be a symlink.')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = root / 'format.json'
        if (root / 'config').exists() or (root / 'local.json').exists():
            raise InvalidRequest('legacy_root', 'Existing v1 configuration cannot be used as a v2 root.')
        if marker.is_symlink():
            raise InvalidRequest('unsafe_root', 'Runtime format marker must not be a symlink.')
        if not marker.exists():
            if any(root.iterdir()):
                raise InvalidRequest('unmarked_root', 'Refusing a nonempty root without a v2 format marker.')
            root.chmod(0o700)
            try:
                with marker.open('x') as stream:
                    stream.write('{"version":2}\n')
            except FileExistsError:
                pass
        if json.loads(marker.read_text()) != {'version': 2}:
            raise InvalidRequest('root_version', 'Runtime root format must be version 2.')
        root.chmod(0o700)
    except (OSError, ValueError):
        raise InvalidRequest('invalid_root', 'Runtime root or format marker is unavailable or malformed.') from None


def initialize_root(settings: Settings) -> None:
    ensure_root(settings.root)
    # Validate any existing config even if a caller constructed Settings directly.
    load_settings(root=settings.root, environ={})
    try:
        for name in ('token', 'workspace', 'config.toml'):
            if (settings.root / name).is_symlink():
                raise InvalidRequest('unsafe_root', 'Runtime files must not be symlinks.')
        config = settings.root / 'config.toml'
        try:
            with config.open('x') as stream:
                for key, value in settings.model_dump(exclude={'root'}).items():
                    stream.write(f'{key} = {json.dumps(value)}\n')
        except FileExistsError:
            pass
        token = settings.root / 'token'
        try:
            fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            read_token(settings.root)
        else:
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_urlsafe(32) + '\n')
        (settings.root / 'workspace').mkdir(exist_ok=True, mode=0o700)
    except OSError:
        raise InvalidRequest('invalid_root', 'Cannot initialize the runtime root.') from None


def read_token(root: Path) -> str:
    try:
        fd = os.open(root / 'token', os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            if os.fstat(stream.fileno()).st_mode & 0o077:
                raise InvalidRequest('unsafe_token', 'Token permissions must be 0600.')
            token = stream.read(256).strip()
        if len(token) < 32 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in token):
            raise InvalidRequest('invalid_token', 'Runtime token is malformed.')
        return token
    except OSError:
        raise InvalidRequest('invalid_token', 'Runtime token is unavailable; run hyperclaw init.') from None
