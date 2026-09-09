import os
from pathlib import Path
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from hyperclaw.config import ensure_root, initialize_root, load_settings
from hyperclaw.contracts import InvalidRequest


def test_setup_preserves_existing_root_and_token(tmp_path, monkeypatch):
    existing = tmp_path / 'v1'
    existing.mkdir()
    (existing / 'keep.txt').write_text('original')
    monkeypatch.setenv('HYPERCLAW_ROOT', str(existing))
    fresh = tmp_path / 'v2'
    settings = load_settings(root=fresh, environ={})
    assert settings.model == 'qwen3.8:27b-mlx'
    assert not fresh.exists()
    initialize_root(settings)
    token = (fresh / 'token').read_bytes()
    initialize_root(settings)
    assert (fresh / 'token').read_bytes() == token
    assert (existing / 'keep.txt').read_text() == 'original'
    assert sorted(p.name for p in existing.iterdir()) == ['keep.txt']
    assert fresh.stat().st_mode & 0o777 == 0o700
    assert (fresh / 'token').stat().st_mode & 0o777 == 0o600


def test_configuration_precedence_and_url_normalization(tmp_path):
    ensure_root(tmp_path)
    (tmp_path / 'config.toml').write_text('model="file-model"\nport=8999\n')
    settings = load_settings(environ={'HYPERCLAW_ROOT': str(tmp_path), 'OLLAMA_MODEL': 'ignored'}, overrides={'model': 'cli'})
    assert settings.model == 'cli'
    assert settings.port == 8999
    assert settings.thinking is False
    assert load_settings(root=tmp_path, environ={}).model == 'file-model'
    for url in ['http://localhost:11434/', 'http://localhost:11434/v1/']:
        assert load_settings(root=tmp_path, overrides={'ollama_url': url}).ollama_url == 'http://localhost:11434'


@pytest.mark.parametrize('values', [
    {'ollama_url': 'file:///tmp/model'}, {'ollama_url': 'http://user:secret@localhost'},
    {'ollama_url': 'http://localhost/?secret=yes'}, {'ollama_url': 'http://localhost/#x'},
    {'ollama_url': 'http://localhost:bad'}, {'model': ''}, {'model': '   '}, {'model': None},
    {'max_output_tokens': 0}, {'request_timeout_s': -1}, {'run_timeout_s': float('inf')},
    {'thinking': 'yes'}, {'port': 65536}, {'unknown': True},
])
def test_invalid_settings(values, tmp_path):
    with pytest.raises(InvalidRequest):
        load_settings(root=tmp_path, environ={}, overrides=values)


@pytest.mark.parametrize('existing', ['keep.txt', 'config/local.json'])
def test_unmarked_roots_are_never_adopted(tmp_path, existing):
    file = tmp_path / existing
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b'personal-data')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(InvalidRequest):
        initialize_root(load_settings(environ={'HYPERCLAW_ROOT': str(tmp_path)}))
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_marker_and_symlink_refusal(tmp_path):
    root = tmp_path / 'root'
    initialize_root(load_settings(root=root, environ={}))
    marker = root / 'format.json'
    marker.write_text('{"version":1}')
    with pytest.raises(InvalidRequest):
        ensure_root(root)
    link = tmp_path / 'link'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(InvalidRequest):
        ensure_root(link)
    marker.write_text('{"version":2}')
    (root / 'token').unlink()
    secret = tmp_path / 'secret'
    secret.write_text('do not touch')
    (root / 'token').symlink_to(secret)
    with pytest.raises(InvalidRequest):
        initialize_root(load_settings(root=root, environ={}))
    assert secret.read_text() == 'do not touch'


def test_malformed_config_is_not_overwritten(tmp_path):
    ensure_root(tmp_path)
    config = tmp_path / 'config.toml'
    config.write_text('not = [toml')
    with pytest.raises(InvalidRequest):
        load_settings(root=tmp_path)
    assert config.read_text() == 'not = [toml'
    assert not (tmp_path / 'token').exists()


def test_import_and_doctor_are_read_only(tmp_path):
    result = subprocess.run([sys.executable, '-c', 'import hyperclaw.config, hyperclaw.cli'],
                            cwd=tmp_path, env={'HYPERCLAW_ROOT': str(tmp_path / 'absent')}, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
    from hyperclaw.cli import app
    result = CliRunner().invoke(app, ['--root', str(tmp_path / 'absent'), 'doctor'])
    assert result.exit_code == 0, result.output
    assert 'qwen3.8:27b-mlx' in result.output and 'FTS5' in result.output
    assert list(tmp_path.iterdir()) == []


def test_adopting_empty_root_makes_it_private(tmp_path):
    root = tmp_path / 'precreated'
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    initialize_root(load_settings(root=root, environ={}))
    assert root.stat().st_mode & 0o777 == 0o700
