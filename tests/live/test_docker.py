"""Explicit Docker lifecycle checks using disposable workspaces and labels."""

import asyncio
import os
from uuid import uuid4

import pytest

from hyperclaw.execution.docker import DockerBackend


pytestmark = pytest.mark.docker


async def wait_for_path(path, timeout_s=5.0):
    async with asyncio.timeout(timeout_s):
        while not path.exists():
            await asyncio.sleep(0.02)


def writable_workspace(path):
    path.mkdir()
    os.chmod(path, 0o777)
    return path


async def test_terminate_kills_parent_and_child_before_they_write(tmp_path):
    workspace = writable_workspace(tmp_path / "workspace")
    backend = DockerBackend("test-" + uuid4().hex, workspace)
    parent = "import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(2); pathlib.Path('/workspace/parent-late').touch()"
    child = "import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path('/workspace/ready').touch(); time.sleep(2); pathlib.Path('/workspace/child-late').touch()"
    argv = ["python", "-c", f"import subprocess; subprocess.Popen(['python', '-c', {child!r}]); exec({parent!r})"]
    container_id = await backend.create("cancel-parent-child", argv, writable=True)
    try:
        await backend.start(container_id)
        await wait_for_path(workspace / "ready")
        result = await backend.terminate(container_id)
        assert result.terminated is True
        assert result.status == "failed"
        await asyncio.sleep(2.2)
        assert not (workspace / "parent-late").exists()
        assert not (workspace / "child-late").exists()
    finally:
        await backend.remove(container_id)


async def test_owned_rediscovers_created_and_running_containers_by_installation(tmp_path):
    workspace = writable_workspace(tmp_path / "workspace")
    installation_id = "test-" + uuid4().hex
    backend = DockerBackend(installation_id, workspace)
    restarted_backend = DockerBackend(installation_id, workspace)
    foreign = DockerBackend("test-" + uuid4().hex, workspace)
    created_id = await backend.create("created-gap", ["python", "-c", "pass"], writable=False)
    running_id = await backend.create("running-gap", ["python", "-c", "import time; time.sleep(30)"], writable=False)
    foreign_id = await foreign.create("foreign", ["python", "-c", "pass"], writable=False)
    try:
        await backend.start(running_id)
        owned = await restarted_backend.owned()
        assert owned == [
            {"id": created_id, "invocation_id": "created-gap", "state": "created"},
            {"id": running_id, "invocation_id": "running-gap", "state": "running"},
        ]
        assert all(item["id"] != foreign_id for item in owned)
    finally:
        await backend.terminate(running_id)
        await backend.remove(created_id)
        await backend.remove(running_id)
        await foreign.remove(foreign_id)


async def test_host_identity_can_use_private_writable_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    (workspace / "private.txt").write_text("private-value")
    os.chmod(workspace / "private.txt", 0o600)
    backend = DockerBackend("test-" + uuid4().hex, workspace)
    argv = ["python", "-c", "from pathlib import Path; print(Path('/workspace/private.txt').read_text()); Path('/workspace/written.txt').write_text('written')"]
    container_id = await backend.create("private-workspace", argv, writable=True)
    try:
        await backend.start(container_id)
        result = await backend.wait(container_id, 5, 1024)
        assert result.status == "succeeded"
        assert result.output.strip() == "private-value"
        assert (workspace / "written.txt").read_text() == "written"
    finally:
        await backend.terminate(container_id)
        await backend.remove(container_id)


async def test_container_has_read_only_mount_no_network_or_host_secrets_and_bounded_output(tmp_path, monkeypatch):
    workspace = writable_workspace(tmp_path / "workspace,isolated")
    host_secret = tmp_path / "host-secret"
    secret = "host-secret-" + uuid4().hex
    host_secret.write_text(secret)
    monkeypatch.setenv("HYPERCLAW_TEST_SECRET", secret)
    backend = DockerBackend("test-" + uuid4().hex, workspace)
    program = f"""
import os, pathlib, socket
try:
    pathlib.Path('/workspace/forbidden').write_text('no')
    print('write-bypassed')
except OSError:
    print('read-only')
s = socket.socket()
s.settimeout(0.2)
print('network-blocked' if s.connect_ex(('198.51.100.1', 9)) else 'network-bypassed')
print('env-absent' if os.environ.get('HYPERCLAW_TEST_SECRET') is None else os.environ['HYPERCLAW_TEST_SECRET'])
print('file-absent' if not pathlib.Path({str(host_secret)!r}).exists() else pathlib.Path({str(host_secret)!r}).read_text())
print('x' * 4096)
"""
    container_id = await backend.create("isolated", ["python", "-c", program], writable=False)
    try:
        await backend.start(container_id)
        result = await backend.wait(container_id, 5, 1024)
        assert result.status == "succeeded"
        assert result.truncated is True
        assert len(result.output.encode()) <= 1024
        assert "read-only" in result.output and not (workspace / "forbidden").exists()
        assert "network-blocked" in result.output
        assert "env-absent" in result.output and secret not in result.output
        assert "file-absent" in result.output
    finally:
        await backend.terminate(container_id)
        await backend.remove(container_id)
