"""Local setup must persist routing without changing existing credentials."""
import json
import os
import subprocess
import sys

from typer.testing import CliRunner


def test_local_profile_is_reused_by_fresh_process(tmp_path, monkeypatch):
    from cli.hyperclaw import app
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    for key in ("HYPERCLAW_PROVIDER", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "OLLAMA_THINK",
                "HYPERCLAW_ENABLE_TOOLS", "HYPERCLAW_ENABLE_TELEGRAM", "HYPERCLAW_ENABLE_SCHEDULER",
                "HYPERCLAW_ENABLE_DATABASE"):
        monkeypatch.setenv(key, "")
    secret = tmp_path / ".env"
    secret.write_text("ANTHROPIC_API_KEY=keep-existing-value\n")
    result = CliRunner().invoke(app, ["local", "--setup-only", "--no-probe"])
    assert result.exit_code == 0, result.output
    assert secret.read_text() == "ANTHROPIC_API_KEY=keep-existing-value\n"
    env = dict(os.environ)
    for key in ("HYPERCLAW_PROVIDER", "OLLAMA_MODEL", "OLLAMA_BASE_URL"):
        env.pop(key, None)
    code = "from hyperclaw.local import load_profile; load_profile(); from hyperclaw.providers import registry; import json; print(json.dumps([(p.name,m) for p,m in registry().resolve('tools', {'chat','tool_use'})]))"
    child = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == [["ollama", "qwen3.8:27b-mlx"]]


def test_package_import_does_not_create_runtime_files(tmp_path):
    env = {**os.environ, "HYPERCLAW_ROOT": str(tmp_path)}
    result = subprocess.run([sys.executable, "-c", "import hyperclaw"], env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert list(tmp_path.iterdir()) == []
