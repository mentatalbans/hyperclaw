"""Docker CLI boundary failures remain safe without a running daemon."""

import json
import os
import sys

import pytest

from hyperclaw.contracts import RuntimeErrorBase
from hyperclaw.execution.docker import DockerBackend, DockerUnavailable


def executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)
    return path


def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir(parents=True)
    return path


async def test_missing_docker_cli_is_actionable_and_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    backend = DockerBackend("install-a", workspace(tmp_path))
    with pytest.raises(DockerUnavailable) as raised:
        await backend.owned()
    assert raised.value.code == "docker_unavailable"
    assert raised.value.message == "Docker is unavailable; start Docker and verify the docker CLI is on PATH."


async def test_create_passes_only_hardened_container_controls(tmp_path, monkeypatch):
    recorded = tmp_path / "argv.json"
    executable(tmp_path / "docker", f"""
import json, pathlib, sys
pathlib.Path({str(recorded)!r}).write_text(json.dumps(sys.argv[1:]))
print('a' * 64)
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    root = workspace(tmp_path)
    backend = DockerBackend("install-a", root, image="tool-image@test-digest")
    container_id = await backend.create("invoke-a", ["python", "-c", "print('ok')"], writable=False)
    assert container_id == "a" * 64
    argv = json.loads(recorded.read_text())
    assert argv[0] == "create"
    assert argv[-4:] == ["tool-image@test-digest", "python", "-c", "print('ok')"]
    assert ["--label", "io.hyperclaw.installation=install-a"] == argv[argv.index("--label"):argv.index("--label") + 2]
    assert "io.hyperclaw.invocation=invoke-a" in argv
    assert "--read-only" in argv
    assert ["--network", "none"] == argv[argv.index("--network"):argv.index("--network") + 2]
    assert ["--cap-drop", "ALL"] == argv[argv.index("--cap-drop"):argv.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges=true"] == argv[argv.index("--security-opt"):argv.index("--security-opt") + 2]
    assert ["--user", f"{os.getuid()}:{os.getgid()}"] == argv[argv.index("--user"):argv.index("--user") + 2]
    assert ["--pids-limit", "64"] == argv[argv.index("--pids-limit"):argv.index("--pids-limit") + 2]
    assert ["--memory", "256m"] == argv[argv.index("--memory"):argv.index("--memory") + 2]
    assert ["--memory-swap", "256m"] == argv[argv.index("--memory-swap"):argv.index("--memory-swap") + 2]
    assert ["--cpus", "1"] == argv[argv.index("--cpus"):argv.index("--cpus") + 2]
    assert ["--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m"] == argv[argv.index("--tmpfs"):argv.index("--tmpfs") + 2]
    assert ["--log-driver", "local"] == argv[argv.index("--log-driver"):argv.index("--log-driver") + 2]
    assert "max-size=1m" in argv and "max-file=1" in argv and "compress=false" in argv
    assert "--init" in argv
    mount = argv[argv.index("--mount") + 1]
    assert mount == f"type=bind,source={root.resolve()},target=/workspace,readonly"
    assert argv.count("--mount") == 1
    assert not any(value == "--env" or value.startswith("--env=") for value in argv)


async def test_create_quotes_mount_source_as_one_read_only_csv_field(tmp_path, monkeypatch):
    recorded = tmp_path / "argv.json"
    executable(tmp_path / "docker", f"""
import json, pathlib, sys
pathlib.Path({str(recorded)!r}).write_text(json.dumps(sys.argv[1:]))
print('b' * 64)
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    root = workspace(tmp_path / "name,with,commas")
    await DockerBackend("install-a", root, image="tool-image@test-digest").create("invoke-a", ["true"], writable=False)
    argv = json.loads(recorded.read_text())
    assert argv[argv.index("--mount") + 1] == f'type=bind,"source={root.resolve()}",target=/workspace,readonly'


async def test_terminate_requires_inspection_evidence_after_cli_processes_close(tmp_path, monkeypatch):
    executable(tmp_path / "docker", """
import json, sys
if sys.argv[1] == 'inspect':
    print(json.dumps([{'Id': 'owned-id', 'Config': {'Labels': {'io.hyperclaw.installation': 'install-a', 'io.hyperclaw.invocation': 'invoke-a'}}, 'State': {'Status': 'running', 'ExitCode': None}}]))
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = await DockerBackend("install-a", workspace(tmp_path)).terminate("owned-id")
    assert result.status == "uncertain"
    assert result.terminated is False
    assert result.exit_code is None


async def test_remove_rejects_a_foreign_installation(tmp_path, monkeypatch):
    executable(tmp_path / "docker", """
import json, sys
if sys.argv[1] == 'inspect':
    print(json.dumps([{'Id': 'foreign-id', 'Config': {'Labels': {'io.hyperclaw.installation': 'install-b', 'io.hyperclaw.invocation': 'invoke-b'}}, 'State': {'Status': 'created', 'ExitCode': None}}]))
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    backend = DockerBackend("install-a", workspace(tmp_path))
    with pytest.raises(ValueError, match="not owned"):
        await backend.remove("foreign-id")


async def test_wait_bounds_utf8_output_and_reports_truncation(tmp_path, monkeypatch):
    executable(tmp_path / "docker", """
import json, sys
if sys.argv[1] == 'inspect':
    print(json.dumps([{'Id': 'owned-id', 'Config': {'Labels': {'io.hyperclaw.installation': 'install-a', 'io.hyperclaw.invocation': 'invoke-a'}}, 'State': {'Status': 'exited', 'ExitCode': 0}}]))
elif sys.argv[1] == 'wait':
    print('0')
elif sys.argv[1] == 'logs':
    print('é' * 20, end='')
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = await DockerBackend("install-a", workspace(tmp_path)).wait("owned-id", timeout_s=1, output_limit=11)
    assert len(result.output.encode()) <= 11
    assert result.truncated is True
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.terminated is True


async def test_deadline_remains_failed_when_termination_handler_exits_zero(tmp_path, monkeypatch):
    state = tmp_path / "state"
    executable(tmp_path / "docker", f"""
import json, pathlib, sys, time
state = pathlib.Path({str(state)!r})
command = sys.argv[1]
if command == 'inspect':
    status = 'exited' if state.exists() else 'running'
    print(json.dumps([{{'Id': 'owned-id', 'Config': {{'Labels': {{'io.hyperclaw.installation': 'install-a', 'io.hyperclaw.invocation': 'invoke-a'}}}}, 'State': {{'Status': status, 'ExitCode': 0 if status == 'exited' else None}}}}]))
elif command == 'wait':
    time.sleep(2)
elif command == 'stop':
    state.touch()
elif command == 'logs':
    pass
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = await DockerBackend("install-a", workspace(tmp_path)).wait("owned-id", timeout_s=0.05, output_limit=100)
    assert result.status == "failed"
    assert result.terminated is True
    assert result.exit_code == 0


async def test_owned_fails_closed_when_container_enumeration_is_truncated(tmp_path, monkeypatch):
    executable(tmp_path / "docker", """
import sys
if sys.argv[1] == 'ps':
    print('c' * (2 * 1024 * 1024 + 1))
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(DockerUnavailable):
        await DockerBackend("install-a", workspace(tmp_path)).owned()


async def test_workspace_replacement_and_invalid_argv_are_sanitized_runtime_errors(tmp_path):
    root = workspace(tmp_path)
    backend = DockerBackend("install-a", root)
    root.rename(tmp_path / "original-workspace")
    root.mkdir()
    with pytest.raises(RuntimeErrorBase):
        await backend.create("invoke-a", ["true"], writable=False)
    with pytest.raises(RuntimeErrorBase):
        await DockerBackend("install-a", root).create("invoke-a", ["bad\0argument"], writable=False)


async def test_removing_state_is_not_termination_evidence(tmp_path, monkeypatch):
    executable(tmp_path / "docker", """
import json, sys
if sys.argv[1] == 'inspect':
    print(json.dumps([{'Id': 'owned-id', 'Config': {'Labels': {'io.hyperclaw.installation': 'install-a', 'io.hyperclaw.invocation': 'invoke-a'}}, 'State': {'Status': 'removing', 'ExitCode': 0}}]))
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = await DockerBackend("install-a", workspace(tmp_path)).terminate("owned-id")
    assert result.status == "uncertain"
    assert result.terminated is False


async def test_start_rechecks_workspace_identity_after_container_id_is_persisted(tmp_path, monkeypatch):
    started = tmp_path / "started"
    executable(tmp_path / "docker", f"""
import json, pathlib, sys
if sys.argv[1] == 'create':
    print('d' * 64)
elif sys.argv[1] == 'inspect':
    print(json.dumps([{{'Id': 'd' * 64, 'Config': {{'Labels': {{'io.hyperclaw.installation': 'install-a', 'io.hyperclaw.invocation': 'invoke-a'}}}}, 'State': {{'Status': 'created', 'ExitCode': None}}}}]))
elif sys.argv[1] == 'start':
    pathlib.Path({str(started)!r}).touch()
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    root = workspace(tmp_path)
    backend = DockerBackend("install-a", root, image="tool-image@test-digest")
    container_id = await backend.create("invoke-a", ["true"], writable=False)
    root.rename(tmp_path / "original-workspace")
    root.mkdir()
    with pytest.raises(RuntimeErrorBase):
        await backend.start(container_id)
    assert not started.exists()


async def test_workspace_change_during_start_terminates_and_reports_backend_loss(tmp_path, monkeypatch):
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    root = workspace(tmp_path)
    original = tmp_path / "original-workspace"
    executable(tmp_path / "docker", f"""
import json, pathlib, sys
started, stopped = pathlib.Path({str(started)!r}), pathlib.Path({str(stopped)!r})
if sys.argv[1] == 'create':
    print('e' * 64)
elif sys.argv[1] == 'inspect':
    status = 'exited' if stopped.exists() else 'running' if started.exists() else 'created'
    print(json.dumps([{{'Id': 'e' * 64, 'Config': {{'Labels': {{'io.hyperclaw.installation': 'install-a', 'io.hyperclaw.invocation': 'invoke-a'}}}}, 'State': {{'Status': status, 'ExitCode': 137 if status == 'exited' else None}}}}]))
elif sys.argv[1] == 'start':
    pathlib.Path({str(root)!r}).rename({str(original)!r})
    pathlib.Path({str(root)!r}).mkdir()
    started.touch()
elif sys.argv[1] == 'stop':
    stopped.touch()
""")
    monkeypatch.setenv("PATH", str(tmp_path))
    backend = DockerBackend("install-a", root, image="tool-image@test-digest")
    container_id = await backend.create("invoke-a", ["true"], writable=False)
    with pytest.raises(DockerUnavailable):
        await backend.start(container_id)
    assert started.exists() and stopped.exists()


@pytest.mark.asyncio
async def test_docs_verification_rejects_missing_init(tmp_path, monkeypatch):
    from hyperclaw.execution.docker import DockerBackend
    from hyperclaw.contracts import InvalidRequest
    image = 'sha256:' + 'a'*64
    raw = {
        'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
            'CapAdd': None, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges=true'],
            'Memory': 268435456, 'MemorySwap': 268435456, 'NanoCpus': 1000000000,
            'PidsLimit': 64, 'Init': False, 'Tmpfs': {'/tmp': 'rw,noexec,nosuid,nodev,size=16m'},
            'LogConfig': {'Type': 'local', 'Config': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}},
        'Config': {'Image': image, 'User': '65532:65532', 'WorkingDir': '/docs',
            'Entrypoint': ['/usr/local/bin/python'], 'Cmd': ['/opt/server.py'], 'OpenStdin': True, 'Env': ['PATH=/usr/local/bin']},
        'Mounts': [{'Type': 'bind', 'RW': False, 'Destination': '/docs', 'Source': str(tmp_path)}],
    }
    class Backend(DockerBackend):
        async def inspect(self, cid): return {}
        async def _inspect_raw(self, cid): return raw
    backend = Backend('test-installation', tmp_path)
    with pytest.raises(InvalidRequest, match='profile'):
        await backend.verify_docs('c'*64, tmp_path, image)
    raw['HostConfig']['Init'] = True
    assert (await backend.verify_docs('c'*64, tmp_path, image))['init'] is True

    # Changing the canonical resource limit also changes inspection authority.
    from hyperclaw.execution import docker
    profile = docker.docs_profile()
    profile['host']['PidsLimit'] = 32
    monkeypatch.setattr(docker, 'docs_profile', lambda: profile)
    with pytest.raises(InvalidRequest, match='profile'):
        await backend.verify_docs('c'*64, tmp_path, image)
    raw['HostConfig']['PidsLimit'] = 32
    assert (await backend.verify_docs('c'*64, tmp_path, image))['pids'] == 32
    # Every constrained inspect field is checked, including list/map contents.
    import copy
    accepted = copy.deepcopy(raw)
    for section, key, bad in [
        ('HostConfig', 'NetworkMode', 'bridge'), ('HostConfig', 'ReadonlyRootfs', False),
        ('HostConfig', 'Privileged', True), ('HostConfig', 'CapAdd', ['SYS_ADMIN']),
        ('HostConfig', 'CapDrop', []), ('HostConfig', 'SecurityOpt', []),
        ('HostConfig', 'Memory', 536870912), ('HostConfig', 'MemorySwap', -1),
        ('HostConfig', 'NanoCpus', 2000000000), ('HostConfig', 'Tmpfs', {}),
        ('HostConfig', 'LogConfig', {'Type': 'none'}),
        ('Config', 'User', '0:0'), ('Config', 'WorkingDir', '/'),
        ('Config', 'Entrypoint', ['/bin/sh']), ('Config', 'Cmd', ['unreviewed.py']),
        ('Config', 'OpenStdin', False), ('Config', 'Image', 'other'),
        ('Config', 'Env', ['SECRET=unreviewed']),
    ]:
        raw = copy.deepcopy(accepted)
        raw[section][key] = bad
        with pytest.raises(InvalidRequest, match='profile|environment'):
            await backend.verify_docs('c'*64, tmp_path, image)
