"""Operator-admitted public documentation and optional SDK client integration.

This module imports no SDK and starts no process until an admitted invocation.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
import shutil
import stat
import tempfile

from pydantic import ConfigDict, Field, field_validator
from hyperclaw.contracts import Conflict, InvalidRequest, RuntimeErrorBase, ToolDefinition, Value, canonical

WIRE_LIMIT = 262_144
RESULT_LIMIT = 65_536
FILE_LIMIT = 65_536
TOTAL_LIMIT = 8 * 1024 * 1024


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def safe_path(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 512
            or any(ord(c) < 32 for c in value) or '\\' in value
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or Path(value).suffix not in {'.md', '.txt'}):
        raise ValueError('Expected a safe relative .md/.txt path')
    return value


class Arguments(Value):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True)


class DocsSearch(Arguments):
    query: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=5, ge=1, le=5)

    @field_validator('query')
    @classmethod
    def bounded_query(cls, value):
        if not value.strip() or '\0' in value or len(value.encode('utf-8')) > 256:
            raise ValueError('Query must be nonblank and at most 256 UTF-8 bytes')
        return value


class DocsRead(Arguments):
    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=80, ge=1, le=200)

    @field_validator('path')
    @classmethod
    def valid_path(cls, value):
        return safe_path(value)


ARGUMENTS = {'search': DocsSearch, 'read': DocsRead}
DESCRIPTIONS = {
    'search': 'Search admitted public documentation for a literal query; return source paths, lines and full-file SHA-256 hashes.',
    'read': 'Read lines from an admitted public .md/.txt document; return its source path, lines and full-file SHA-256 hash.',
}


def catalog():
    return [{'name': name, 'description': DESCRIPTIONS[name],
             'inputSchema': schema.model_json_schema(), 'outputSchema': None}
            for name, schema in ARGUMENTS.items()]


def collect(directory: Path, *, identities=None, public_only=False):
    """Read through directory descriptors; never follow links or open skipped files."""
    files = {}
    total = 0
    entries = 0
    def walk(fd, prefix='', depth=0):
        nonlocal total, entries
        if identities is not None:
            info = os.fstat(fd)
            identities[prefix] = [info.st_dev, info.st_ino]
        if depth > 32:
            raise ValueError('Documentation depth exceeds limit')
        for name in sorted(os.listdir(fd)):
            entries += 1
            if entries > 4096 or name in {'.', '..'} or '/' in name or '\\' in name or any(ord(c) < 32 for c in name):
                raise ValueError('Unsafe or excessive documentation entries')
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    actual = os.fstat(child)
                    if (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
                        raise ValueError('Directory changed')
                    walk(child, path + '/', depth + 1)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Only ordinary unlinked files are permitted')
            elif Path(name).suffix in {'.md', '.txt'}:
                safe_path(path)
                if len(files) >= 512 or info.st_size > FILE_LIMIT:
                    raise ValueError('Documentation exceeds file limits')
                child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    before = os.fstat(child)
                    if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise ValueError('File changed')
                    with os.fdopen(child, 'rb', closefd=False) as stream:
                        data = stream.read(FILE_LIMIT + 1)
                    after = os.fstat(child)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('File changed during read')
                finally:
                    os.close(child)
                if len(data) > FILE_LIMIT or b'\0' in data:
                    raise ValueError('Invalid public text')
                data.decode('utf-8')
                total += len(data)
                if total > TOTAL_LIMIT:
                    raise ValueError('Documentation exceeds combined limit')
                files[path] = data
                if identities is not None:
                    identities[path] = [after.st_dev, after.st_ino]
            elif public_only:
                raise ValueError('Snapshot contains an unadmitted extension')
    try:
        # Reject symlinked ancestors as well as the root.
        directory = directory.absolute()
        for ancestor in (directory, *directory.parents):
            if ancestor.is_symlink():
                raise ValueError('Symlinked documentation root')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            walk(fd)
        finally:
            os.close(fd)
    except (OSError, ValueError, UnicodeError):
        raise InvalidRequest('invalid_mcp_docs', 'Documentation must be bounded public UTF-8 text without links or unsafe paths.') from None
    return files


def file_manifest(files):
    return [{'path': path, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
            for path, data in sorted(files.items())]


def docs_policy():
    # Keep admission inspection available without importing the optional SDK.
    from hyperclaw.execution.docker import docs_profile
    return {'version': 1, 'docker': docs_profile(),
            'wire_limit': WIRE_LIMIT, 'result_limit': RESULT_LIMIT}


def validate_manifest(manifest):
    try:
        value = dict(manifest)
        fingerprint = value.pop('sha256')
        identity = value.pop('snapshot_identity')
        if not isinstance(identity, dict) or '' not in identity or any(
                not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int or n < 0 for n in pair)
                for pair in identity.values()):
            raise ValueError
        if set(value) != {'server', 'source', 'image', 'protocol', 'catalog', 'files', 'content_hash', 'policy'}:
            raise ValueError
        if (value['server'] != 'docs' or digest(value) != fingerprint
                or digest(value['files']) != value['content_hash'] or value['catalog'] != catalog()):
            raise ValueError
        from hyperclaw.config import Settings
        Settings(root=Path('/unused'), mcp_docs_path=value['source'], mcp_docs_image=value['image'], mcp_protocol=value['protocol'])
        if value['policy'] != docs_policy() or len(value['files']) > 512:
            raise ValueError
        paths = set()
        total = 0
        for file in value['files']:
            if set(file) != {'path', 'sha256', 'size_bytes'} or safe_path(file['path']) in paths:
                raise ValueError
            paths.add(file['path'])
            if (type(file['size_bytes']) is not int or not 0 <= file['size_bytes'] <= FILE_LIMIT
                    or not isinstance(file['sha256'], str) or re.fullmatch('[0-9a-f]{64}', file['sha256']) is None):
                raise ValueError
            total += file['size_bytes']
        if total > TOTAL_LIMIT:
            raise ValueError
        return dict(value, sha256=fingerprint, snapshot_identity=identity)
    except (KeyError, TypeError, ValueError):
        raise InvalidRequest('invalid_mcp_manifest', 'MCP admission evidence is invalid.') from None


class McpTools:
    def __init__(self, store, settings, backend):
        self.store, self.settings, self.backend = store, settings, backend

    @property
    def enabled(self):
        return bool(self.settings.mcp_docs_path and self.settings.mcp_docs_image)

    def list_tools(self):
        if not self.enabled:
            return []
        return [ToolDefinition(name='mcp_docs_' + name, description=DESCRIPTIONS[name],
                               input_schema=schema.model_json_schema(), capability='mcp_docs', effect='read')
                for name, schema in ARGUMENTS.items()]

    def _source(self):
        if not self.enabled:
            raise InvalidRequest('mcp_unavailable', 'MCP documentation is disabled; configure its public path and immutable image.')
        path = Path(self.settings.mcp_docs_path)
        resolved = path.resolve()
        runtime = self.settings.root.resolve()
        home = Path.home().resolve()
        hidden_home = home in resolved.parents and resolved.relative_to(home).parts[0].startswith('.')
        if (hidden_home or resolved == home or resolved in home.parents or resolved == runtime
                or resolved in runtime.parents or runtime in resolved.parents):
            raise InvalidRequest('unsafe_mcp_docs', 'Documentation cannot include the runtime root or operator home.')
        return path

    def _preview(self):
        source = self._source()
        files = collect(source)
        listing = file_manifest(files)
        value = {'server': 'docs', 'source': str(source), 'image': self.settings.mcp_docs_image,
                 'protocol': self.settings.mcp_protocol, 'catalog': catalog(), 'files': listing,
                 'content_hash': digest(listing),
                 'policy': docs_policy()}
        return dict(value, sha256=digest(value)), files

    async def inspect_admission(self):
        if not self.enabled:
            return {'enabled': False, 'admission': await self.store.mcp_admission()}
        manifest, _ = await asyncio.to_thread(self._preview)
        return manifest

    def _snapshot(self, manifest, files):
        parent = self.settings.root / 'mcp-docs'
        if parent.is_symlink():
            raise InvalidRequest('invalid_mcp_snapshot', 'Snapshot parent must not be a link.')
        parent.mkdir(mode=0o700, exist_ok=True)
        target = parent / manifest['content_hash']
        if target.exists():
            if file_manifest(collect(target, public_only=True)) != manifest['files']:
                raise Conflict('mcp_snapshot_changed', 'The admitted snapshot changed.')
            return
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=parent))
        try:
            for path, data in files.items():
                dest = stage / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                dest.chmod(0o444)
            for directory, _, _ in os.walk(stage, topdown=False):
                Path(directory).chmod(0o555)
            if file_manifest(collect(stage, public_only=True)) != manifest['files']:
                raise ValueError('Staged snapshot changed')
            stage.rename(target)
        finally:
            if stage.exists():
                for directory, _, _ in os.walk(stage):
                    Path(directory).chmod(0o700)
                shutil.rmtree(stage)

    async def admit(self, expected_sha256):
        manifest, files = await asyncio.to_thread(self._preview)
        if manifest['sha256'] != expected_sha256:
            raise Conflict('mcp_changed', 'MCP documentation, catalog or configuration changed since inspection.')
        await asyncio.to_thread(self._snapshot, manifest, files)
        identity = {}
        snapshot = self.settings.root / 'mcp-docs' / manifest['content_hash']
        observed = await asyncio.to_thread(collect, snapshot, identities=identity, public_only=True)
        if file_manifest(observed) != manifest['files']:
            raise Conflict('mcp_snapshot_changed', 'Staged snapshot changed before admission.')
        manifest['snapshot_identity'] = identity
        # Staging never authorizes; only this final transaction publishes admission.
        return await self.store.admit_mcp(manifest)

    async def revoke(self):
        return await self.store.revoke_mcp()

    async def current(self):
        manifest = await self.store.mcp_admission()
        if manifest is None:
            raise Conflict('mcp_not_admitted', 'MCP documentation is not admitted.')
        current, _ = await asyncio.to_thread(self._preview)
        if current != {key: value for key, value in manifest.items() if key not in {'snapshot_identity', 'admission_id'}}:
            raise Conflict('mcp_changed', 'MCP documentation, catalog or configuration requires renewed admission.')
        snapshot = self.settings.root / 'mcp-docs' / manifest['content_hash']
        identity = {}
        observed = await asyncio.to_thread(collect, snapshot, identities=identity, public_only=True)
        if file_manifest(observed) != manifest['files'] or identity != manifest['snapshot_identity']:
            raise Conflict('mcp_snapshot_changed', 'The admitted snapshot changed.')
        return manifest

    async def invoke(self, call, *, invocation_id):
        from hyperclaw.contracts import ToolReceipt
        from hyperclaw.execution.policy import Policy
        from hyperclaw.execution.docker import DockerUnavailable
        from hyperclaw.mcp_transport import owned_attachment
        Client = require_sdk()
        from mcp_types import Implementation
        inv = await self.store.get_invocation(invocation_id)
        if inv.receipt:
            return inv.receipt
        run = await self.store.get_run(inv.run_id)
        manifest = await self.current()
        if (inv.call != call or inv.capability != 'mcp_docs' or inv.status != 'prepared'
                or run.status != 'running' or run.mcp.get('sha256') != manifest['sha256']
                or run.mcp.get('admission_id') != manifest['admission_id']):
            raise Conflict('mcp_invocation_changed', 'MCP dispatch requires an unchanged persisted admitted invocation.')
        decision = Policy(inv.workspace_id, await self.store.grants(inv.workspace_id), manifest).check(call, run.request.tools)
        if decision.sha256 != inv.policy_sha256:
            raise Conflict('mcp_changed', 'MCP execution policy changed.')
        name = call.name.removeprefix('mcp_docs_')
        snapshot = self.settings.root / 'mcp-docs' / manifest['content_hash']
        evidence = {'server': 'docs', 'sha256': manifest['sha256'], 'content_hash': manifest['content_hash'],
                    'image': manifest['image'], 'protocol': manifest['protocol'], 'admission_id': manifest['admission_id']}
        cid, output, status, cancelled = None, '', 'failed', False
        await self.store.mark_invocation_running(inv.id)
        try:
            cid = await self.backend.create_docs(inv.id, snapshot, manifest['image'])
            await self.store.bind_container(inv.id, cid)
            evidence['container_id'] = cid
            evidence['profile'] = await self.backend.verify_docs(cid, snapshot, manifest['image'])
            if (await self.current())['admission_id'] != manifest['admission_id']:
                raise Conflict('mcp_changed', 'MCP admission changed before peer start.')
            timeout = min(60, self.settings.run_timeout_s - await self.store.elapsed(inv.run_id))
            async with asyncio.timeout(max(.001, timeout)):
                async with Client(owned_attachment(self.backend, cid, evidence=evidence),
                                  mode='auto' if manifest['protocol'] == '2026-07-28' else 'legacy',
                                  cache=None, input_required_max_rounds=0, read_timeout_seconds=timeout,
                                  client_info=Implementation(name='hyperclaw', version='2.0.0.dev0')) as client:
                    if client.protocol_version != manifest['protocol']:
                        raise InvalidRequest('mcp_protocol_changed', 'MCP negotiated an unadmitted revision.')
                    if client.server_info is None or client.server_info.model_dump(mode='json', by_alias=True, exclude_none=True) != {'name': 'docs', 'version': '1'}:
                        raise Conflict('mcp_server_changed', 'MCP peer identity does not match the admitted documentation server.')
                    listed = await client.list_tools()
                    review_catalog(listed, manifest['catalog'])
                    if (await self.current())['admission_id'] != manifest['admission_id']:
                        raise Conflict('mcp_changed', 'MCP admission changed before tools/call.')
                    result = await client.call_tool(name, decision.arguments)
                    value = validate_result(result, name, decision.arguments)
                    sources = [value] if name == 'read' else value['hits']
                    reviewed = {f['path']: f['sha256'] for f in manifest['files']}
                    if any(reviewed.get(entry['path']) != entry['sha256'] for entry in sources):
                        raise InvalidRequest('mcp_source_changed', 'Peer source hashes do not match the admitted snapshot.')
                    output = canonical(value)
                    evidence['sources'] = sources
                    status = 'succeeded'
        except asyncio.CancelledError:
            cancelled = True
            status = 'cancelled'
        except Exception as exc:
            error = exc
            while isinstance(error, BaseExceptionGroup):
                error = error.exceptions[0]
            evidence['error_type'] = type(error).__name__
            if isinstance(error, RuntimeErrorBase):
                evidence.update(code=error.code, reason=error.message)
            elif type(error).__name__ == 'InputRequiredRoundsExceededError':
                evidence.update(code='mcp_input_required_unsupported', reason='MCP input-required results are unsupported; this call was not retried.')
            elif isinstance(error, TimeoutError):
                evidence.update(code='mcp_timeout', reason='MCP invocation exceeded its deadline.')
            else:
                evidence.update(code='mcp_peer_failed', reason='MCP peer did not return a valid admitted result; no automatic retry.')
        async def finish():
            nonlocal cid, status
            try:
                if cid is None:
                    owned = [item for item in await self.backend.owned() if item['invocation_id'] == inv.id]
                    for item in owned:
                        result = await self.backend.terminate(item['id'], output_limit=0)
                        if not result.terminated:
                            raise DockerUnavailable()
                        await self.backend.remove(item['id'])
                    evidence['terminated'] = True
                else:
                    result = await self.backend.terminate(cid, output_limit=0)
                    evidence['terminated'] = result.terminated
                    if not result.terminated:
                        status = 'uncertain'
            except (DockerUnavailable, ValueError):
                evidence['terminated'] = False
                status = 'uncertain'
            receipt = await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id,
                status=status, output=output if status == 'succeeded' else '', evidence=evidence))
            if cid and evidence['terminated']:
                try:
                    await self.backend.remove(cid)
                except DockerUnavailable:
                    pass
            return receipt
        from hyperclaw.execution import settle
        receipt = await settle(asyncio.create_task(finish()))
        if cancelled:
            raise asyncio.CancelledError
        return receipt


def require_sdk():
    try:
        from mcp import Client
        return Client
    except ImportError:
        raise InvalidRequest('mcp_unavailable', 'MCP requires the optional extra: install hyperclaw[mcp].') from None


def validate_result(result, name, arguments):
    """Accept only whole bounded JSON with the closed documentation result shape."""
    import json
    import re
    from mcp_types import CallToolResult
    try:
        if not isinstance(result, CallToolResult):
            raise ValueError('Unsupported MCP result')
        raw = result.model_dump(mode='json', by_alias=True, exclude_none=True)
        if len(canonical(raw).encode()) > RESULT_LIMIT:
            raise ValueError('MCP result exceeds 65536 bytes')
        metadata = raw.pop('_meta', {})
        if metadata and metadata != {'io.modelcontextprotocol/serverInfo': {'name': 'docs', 'version': '1'}}:
            raise ValueError('Unsupported result metadata')
        if set(raw) - {'content', 'structuredContent', 'isError', 'resultType'} or result.result_type != 'complete' or result.is_error or len(result.content) != 1:
            raise ValueError('Unsupported MCP result features')
        content = result.content[0].model_dump(mode='json', by_alias=True, exclude_none=True)
        if set(content) != {'type', 'text'} or content['type'] != 'text':
            raise ValueError('Only JSON text results are supported')
        value = json.loads(content['text'])
        if result.structured_content is not None and result.structured_content != value:
            raise ValueError('Inconsistent structured result')
        entries = [value] if name == 'read' else value['hits']
        if name == 'search' and (set(value) != {'hits'} or not isinstance(entries, list) or len(entries) > arguments['limit']):
            raise ValueError('Invalid search result')
        for entry in entries:
            expected = {'path', 'start_line', 'end_line', 'sha256', 'text'} if name == 'read' else {'path', 'line', 'sha256', 'text'}
            if set(entry) != expected or safe_path(entry['path']) != entry['path']:
                raise ValueError('Invalid source result')
            if not isinstance(entry['sha256'], str) or re.fullmatch('[0-9a-f]{64}', entry['sha256']) is None:
                raise ValueError('Invalid source hash')
            if not isinstance(entry['text'], str) or len(entry['text'].encode()) > (8192 if name == 'read' else 512):
                raise ValueError('Source excerpt exceeds limit')
            if name == 'read':
                if (entry['path'] != arguments['path'] or type(entry['start_line']) is not int
                        or type(entry['end_line']) is not int or entry['start_line'] != arguments['start_line']
                        or not entry['start_line'] - 1 <= entry['end_line'] < entry['start_line'] + arguments['max_lines']):
                    raise ValueError('Invalid read lines')
            elif type(entry['line']) is not int or entry['line'] < 1:
                raise ValueError('Invalid search line')
        return value
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidRequest('invalid_mcp_result', 'MCP peer returned an unsupported, malformed or oversized result.') from None


def review_catalog(listed, expected):
    actual = [{'name': tool.name, 'description': tool.description, 'inputSchema': tool.input_schema,
               'outputSchema': tool.output_schema} for tool in listed.tools]
    if actual != expected or listed.next_cursor is not None:
        raise Conflict('mcp_catalog_changed', 'MCP peer catalog does not match the reviewed schemas.')
    for tool in listed.tools:
        raw = tool.model_dump(mode='json', by_alias=True, exclude_none=True)
        if set(raw) - {'name', 'description', 'inputSchema', 'outputSchema'}:
            raise Conflict('mcp_catalog_changed', 'MCP peer tool metadata was not admitted.')
