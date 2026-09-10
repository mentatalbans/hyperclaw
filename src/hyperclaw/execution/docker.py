"""Owned Docker container lifecycle for command tool invocations."""

from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re
from typing import Any

from hyperclaw.contracts import InvalidRequest, RuntimeErrorBase


TOOL_IMAGE = "python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e"
INSTALLATION_LABEL = "io.hyperclaw.installation"
INVOCATION_LABEL = "io.hyperclaw.invocation"
_STOPPED_STATES = frozenset({"created", "exited", "dead"})
_LABEL_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CLI_CAPTURE_LIMIT = 2 * 1024 * 1024


class DockerUnavailable(RuntimeErrorBase):
    code = "docker_unavailable"
    message = "Docker is unavailable; start Docker and verify the docker CLI is on PATH."

    def __init__(self, message: str | None = None):
        super().__init__(message=message)


@dataclass(frozen=True)
class DockerResult:
    status: str
    output: str = ""
    exit_code: int | None = None
    terminated: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class _CommandResult:
    stdout: bytes
    stderr: bytes
    truncated: bool


class _CommandTimeout(DockerUnavailable):
    pass


class DockerBackend:
    """Run and reconcile only containers carrying this installation's label."""

    def __init__(self, installation_id: str, workspace: str | os.PathLike[str], image: str = TOOL_IMAGE):
        if not _LABEL_VALUE.fullmatch(installation_id):
            raise ValueError("Invalid Docker installation ID")
        if not image or "\0" in image:
            raise ValueError("Invalid Docker image")
        requested = Path(workspace)
        resolved = requested.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Docker workspace must be a directory")
        stat = resolved.stat()
        self.installation_id = installation_id
        self.workspace = resolved
        self.image = image
        self._workspace_identity = (stat.st_dev, stat.st_ino)
        # Matching the non-root daemon identity preserves access to private
        # workspace modes on Linux. A root daemon uses a fixed unprivileged
        # fallback; that workspace must explicitly grant UID/GID 65532 access.
        uid, gid = os.getuid(), os.getgid()
        self._container_user = (uid, gid) if uid != 0 else (65532, 65532)

    async def create(self, invocation_id: str, argv: list[str], writable: bool) -> str:
        if not _LABEL_VALUE.fullmatch(invocation_id):
            raise InvalidRequest("invalid_docker_invocation", "The command invocation ID is invalid.")
        if not argv or any(not isinstance(value, str) or not value or "\0" in value for value in argv):
            raise InvalidRequest("invalid_command", "Command argv must contain nonempty strings.")
        if not isinstance(writable, bool):
            raise InvalidRequest("invalid_command", "Command writable must be a boolean.")
        self._verify_workspace()
        fields = ["type=bind", f"source={self.workspace}", "target=/workspace"]
        if not writable:
            fields.append("readonly")
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="").writerow(fields)
        mount = buffer.getvalue()
        command = [
            "create",
            "--label", f"{INSTALLATION_LABEL}={self.installation_id}",
            "--label", f"{INVOCATION_LABEL}={invocation_id}",
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--user", f"{self._container_user[0]}:{self._container_user[1]}",
            "--workdir", "/workspace",
            "--pids-limit", "64",
            "--memory", "256m",
            "--memory-swap", "256m",
            "--cpus", "1",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--log-driver", "local",
            "--log-opt", "max-size=1m",
            "--log-opt", "max-file=1",
            "--log-opt", "compress=false",
            "--init",
            "--mount", mount,
            self.image,
            *argv,
        ]
        result = await self._run(command)
        container_id = result.stdout.decode("utf-8", errors="ignore").strip()
        if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            raise DockerUnavailable()
        return container_id

    async def create_docs(self, invocation_id, snapshot, image):
        if not _LABEL_VALUE.fullmatch(invocation_id):
            raise InvalidRequest('invalid_docker_invocation', 'Invalid MCP invocation ID.')
        if re.fullmatch(r'(?:sha256:|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:)[0-9a-f]{64}', image) is None:
            raise InvalidRequest('invalid_mcp_image', 'MCP image must be immutable.')
        snapshot = Path(snapshot)
        if snapshot.is_symlink() or not snapshot.is_dir():
            raise InvalidRequest('invalid_mcp_snapshot', 'MCP snapshot is unavailable.')
        fields = ['type=bind', f'source={snapshot.resolve()}', 'target=/docs', 'readonly']
        buffer = io.StringIO(newline='')
        csv.writer(buffer, lineterminator='').writerow(fields)
        result = await self._run([
            'create', '--pull', 'never', '--interactive', '--label', f'{INSTALLATION_LABEL}={self.installation_id}',
            '--label', f'{INVOCATION_LABEL}={invocation_id}', '--label', 'io.hyperclaw.profile=mcp-docs',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges=true', '--user', '65532:65532',
            '--workdir', '/docs', '--pids-limit', '64', '--memory', '256m',
            '--memory-swap', '256m', '--cpus', '1',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m',
            '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1',
            '--log-opt', 'compress=false', '--init', '--mount', buffer.getvalue(),
            '--entrypoint', '/usr/local/bin/python', image, '/opt/server.py',
        ])
        cid = result.stdout.decode('ascii').strip()
        if re.fullmatch('[a-f0-9]{12,64}', cid) is None:
            raise DockerUnavailable()
        return cid

    async def verify_docs(self, container_id, snapshot, image):
        await self.inspect(container_id)
        raw = await self._inspect_raw(container_id)
        host, config = raw['HostConfig'], raw['Config']
        mounts = raw['Mounts']
        if (len(mounts) != 1 or mounts[0]['Type'] != 'bind' or mounts[0]['RW']
                or mounts[0]['Destination'] != '/docs' or Path(mounts[0]['Source']).resolve() != Path(snapshot).resolve()
                or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs']
                or host['Privileged'] or host.get('CapAdd') or host['CapDrop'] != ['ALL']
                or 'no-new-privileges=true' not in host['SecurityOpt']
                or host['Memory'] != 268435456 or host['MemorySwap'] != 268435456
                or host['NanoCpus'] != 1000000000 or host['PidsLimit'] != 64
                or config['User'] != '65532:65532' or config['WorkingDir'] != '/docs'
                or config['Image'] != image or config['Entrypoint'] != ['/usr/local/bin/python']
                or config['Cmd'] != ['/opt/server.py'] or not config['OpenStdin']
                or host['Tmpfs'] != {'/tmp': 'rw,noexec,nosuid,nodev,size=16m'}
                or host['LogConfig'] != {'Type': 'local', 'Config': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}):
            raise InvalidRequest('mcp_profile_changed', 'The actual Docker documentation profile does not match admission.')
        allowed_env = {'PATH', 'LANG', 'GPG_KEY', 'PYTHON_VERSION', 'PYTHON_SHA256', 'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE'}
        if any(item.split('=', 1)[0] not in allowed_env for item in config.get('Env') or []):
            raise InvalidRequest('mcp_profile_changed', 'The documentation image has unreviewed environment variables.')
        return {'mounts': mounts, 'user': config['User'], 'network': host['NetworkMode'],
                'readonly_root': host['ReadonlyRootfs'], 'memory': host['Memory'],
                'pids': host['PidsLimit'], 'nano_cpus': host['NanoCpus'], 'environment': config['Env']}

    async def attach_start(self, container_id):
        before = await self.inspect(container_id)
        if before['state'] != 'created':
            raise DockerUnavailable('MCP peer is not in its initial created state.')
        try:
            return await asyncio.create_subprocess_exec('docker', 'start', '--attach', '--interactive', before['id'],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError:
            raise DockerUnavailable() from None

    async def start(self, container_id: str) -> None:
        before = await self.inspect(container_id)
        if before["state"] != "created":
            raise ValueError("Owned Docker container is not in created state")
        self._verify_workspace()
        try:
            await self._run(["start", before["id"]])
        except DockerUnavailable:
            pass
        try:
            self._verify_workspace()
        except InvalidRequest:
            try:
                await self.terminate(before["id"])
            except (DockerUnavailable, ValueError):
                pass
            raise DockerUnavailable("The workspace changed while Docker was starting; command outcome is uncertain.") from None
        after = await self.inspect(before["id"])
        if after["state"] == "created":
            raise DockerUnavailable()

    async def inspect(self, container_id: str) -> dict[str, Any]:
        raw = await self._inspect_raw(container_id)
        labels = raw.get("Config", {}).get("Labels") or {}
        if labels.get(INSTALLATION_LABEL) != self.installation_id:
            raise ValueError("Docker container is not owned by this installation")
        invocation_id = labels.get(INVOCATION_LABEL)
        state = raw.get("State") or {}
        actual_id = raw.get("Id")
        if not isinstance(actual_id, str) or not isinstance(invocation_id, str) or not isinstance(state.get("Status"), str):
            raise DockerUnavailable()
        exit_code = state.get("ExitCode")
        return {
            "id": actual_id,
            "invocation_id": invocation_id,
            "state": state["Status"],
            "exit_code": exit_code if isinstance(exit_code, int) else None,
        }

    async def wait(self, container_id: str, timeout_s: float, output_limit: int) -> DockerResult:
        if timeout_s <= 0:
            raise ValueError("Docker timeout must be positive")
        if output_limit < 0 or output_limit > 65536:
            raise ValueError("Docker output limit must be between 0 and 65536 bytes")
        await self.inspect(container_id)
        try:
            await self._run(["wait", container_id], timeout_s=timeout_s)
        except _CommandTimeout:
            terminated = await self.terminate(container_id)
            return DockerResult(
                status="failed",
                output=terminated.output,
                exit_code=terminated.exit_code,
                terminated=terminated.terminated,
                truncated=terminated.truncated,
            )
        info = await self.inspect(container_id)
        output, truncated = await self._logs(container_id, output_limit)
        return DockerResult(
            status=self._result_status(info),
            output=output,
            exit_code=info["exit_code"],
            terminated=info["state"] in _STOPPED_STATES,
            truncated=truncated,
        )

    async def terminate(self, container_id: str, output_limit: int = 65536) -> DockerResult:
        if output_limit < 0 or output_limit > 65536:
            raise ValueError("Docker output limit must be between 0 and 65536 bytes")
        info = await self.inspect(container_id)
        if info["state"] not in _STOPPED_STATES:
            try:
                await self._run(["stop", "--time", "1", container_id], timeout_s=5)
            except (DockerUnavailable, _CommandTimeout):
                pass
            info = await self.inspect(container_id)
        if info["state"] not in _STOPPED_STATES:
            try:
                await self._run(["kill", container_id], timeout_s=5)
            except (DockerUnavailable, _CommandTimeout):
                pass
            info = await self.inspect(container_id)
        output, truncated = await self._logs(container_id, output_limit)
        return DockerResult(
            status=self._result_status(info),
            output=output,
            exit_code=info["exit_code"],
            terminated=info["state"] in _STOPPED_STATES,
            truncated=truncated,
        )

    @staticmethod
    def _result_status(info: dict[str, Any]) -> str:
        if info["state"] not in _STOPPED_STATES:
            return "uncertain"
        if info["state"] in {"exited", "dead"} and info["exit_code"] == 0:
            return "succeeded"
        return "failed"

    async def owned(self) -> list[dict[str, Any]]:
        result = await self._run([
            "ps", "-a", "-q", "--no-trunc", "--filter",
            f"label={INSTALLATION_LABEL}={self.installation_id}",
        ])
        if result.truncated:
            raise DockerUnavailable("Docker container enumeration exceeded its bounded output.")
        owned = []
        for container_id in result.stdout.decode("utf-8", errors="ignore").splitlines():
            if not container_id:
                continue
            try:
                info = await self.inspect(container_id)
            except ValueError:
                continue
            owned.append({key: info[key] for key in ("id", "invocation_id", "state")})
        return sorted(owned, key=lambda value: (value["invocation_id"], value["id"]))

    async def remove(self, container_id: str) -> None:
        await self.inspect(container_id)
        await self._run(["rm", "--force", container_id])

    def _verify_workspace(self) -> None:
        try:
            stat = self.workspace.lstat()
        except OSError as exc:
            raise InvalidRequest("workspace_changed", "The configured workspace changed before command dispatch.") from exc
        if not self.workspace.is_dir() or self.workspace.is_symlink() or (stat.st_dev, stat.st_ino) != self._workspace_identity:
            raise InvalidRequest("workspace_changed", "The configured workspace changed before command dispatch.")

    async def _inspect_raw(self, container_id: str) -> dict[str, Any]:
        if not isinstance(container_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", container_id):
            raise ValueError("Invalid Docker container ID")
        result = await self._run(["inspect", container_id])
        try:
            document = json.loads(result.stdout)
            if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
                raise ValueError
            return document[0]
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise DockerUnavailable() from None

    async def _logs(self, container_id: str, output_limit: int) -> tuple[str, bool]:
        result = await self._run(["logs", container_id])
        combined = result.stdout
        if result.stderr:
            combined += (b"\n" if combined else b"") + result.stderr
        truncated = result.truncated or len(combined) > output_limit
        return combined[:output_limit].decode("utf-8", errors="ignore"), truncated

    async def _run(self, args: list[str], timeout_s: float = 15) -> _CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError):
            raise DockerUnavailable() from None
        try:
            async with asyncio.timeout(timeout_s):
                stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
                stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
                stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
                returncode = await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise _CommandTimeout() from None
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if returncode != 0:
            raise DockerUnavailable()
        return _CommandResult(stdout[0], stderr[0], stdout[1] or stderr[1])

    @staticmethod
    async def _read_bounded(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        captured = bytearray()
        truncated = False
        while chunk := await stream.read(65536):
            available = _CLI_CAPTURE_LIMIT - len(captured)
            if available > 0:
                captured.extend(chunk[:available])
            if len(chunk) > available:
                truncated = True
        return bytes(captured), truncated
