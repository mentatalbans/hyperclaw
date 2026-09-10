from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from hyperclaw.config import Settings
from hyperclaw.contracts import Conflict, InvalidRequest, RunRequest
from hyperclaw.store import Store

@asynccontextmanager
async def opened(root):
    store = await Store.open(root)
    try:
        yield store
    finally:
        await store.close()

IMAGE = 'sha256:' + 'a' * 64


def test_mcp_settings_are_closed_and_opt_in(tmp_path):
    assert 'mcp_docs_path' in Settings.model_fields
    assert Settings(root=tmp_path).mcp_docs_path == ''
    for extra in ({'mcp_docs_path': '/docs'}, {'mcp_docs_image': IMAGE},
                  {'mcp_docs_path': '/docs', 'mcp_docs_image': 'python:latest'},
                  {'mcp_docs_path': 'relative', 'mcp_docs_image': IMAGE}):
        with pytest.raises(ValidationError):
            Settings(root=tmp_path, **extra)
    run = RunRequest(session_id='s', generation=0, request_id='r', text='hello', tools=['mcp_docs_read'])
    assert run.tools == ('mcp_docs_read',)
    assert 'mcp_docs_read' not in RunRequest.model_fields['tools'].default


@pytest.mark.asyncio
async def test_admission_snapshot_and_revoke_preserve_evidence(tmp_path):
    from hyperclaw.mcp import McpTools
    source = tmp_path / 'public'
    source.mkdir()
    (source / 'guide.md').write_text('opaque-doc-marker\n')
    (source / 'secret.bin').write_bytes(b'\xff')
    settings = Settings(root=tmp_path / 'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    async with opened(settings.root) as store:
        tools = McpTools(store, settings, None)
        preview = await tools.inspect_admission()
        with pytest.raises(Conflict):
            await tools.admit('0' * 64)
        assert await store.mcp_admission() is None
        manifest = await tools.admit(preview['sha256'])
        snapshot = settings.root / 'mcp-docs' / manifest['content_hash']
        assert sorted(p.name for p in snapshot.iterdir()) == ['guide.md']
        assert (snapshot / 'guide.md').stat().st_ino != (source / 'guide.md').stat().st_ino
        assert [t.name for t in tools.list_tools()] == ['mcp_docs_search', 'mcp_docs_read']
        assert await tools.current() == manifest
        (source / 'guide.md').write_text('changed\n')
        assert (snapshot / 'guide.md').read_text() == 'opaque-doc-marker\n'
        assert (await tools.inspect_admission())['sha256'] != preview['sha256']
        with pytest.raises(Conflict):
            await tools.current()
        await tools.revoke()
        assert await store.mcp_admission() is None
        assert snapshot.is_dir()
        assert await store._call(lambda: store._db.execute('SELECT count(*) FROM mcp_versions').fetchone()[0]) == 1


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'oversize', 'escape'])
@pytest.mark.asyncio
async def test_source_collection_rejects_unsafe_entries(tmp_path, kind):
    from hyperclaw.mcp import McpTools
    source = tmp_path / 'public'
    source.mkdir()
    sentinel = tmp_path / 'credential.txt'
    sentinel.write_text('secret')
    target = source / 'bad.md'
    if kind == 'symlink': target.symlink_to(sentinel)
    elif kind == 'hardlink': os.link(sentinel, target)
    elif kind == 'fifo': os.mkfifo(target)
    elif kind == 'oversize': target.write_bytes(b'x' * 65537)
    else: target = source / 'bad\\name.md'; target.write_text('bad')
    async with opened(tmp_path / 'runtime') as store:
        tools = McpTools(store, Settings(root=tmp_path / 'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE), None)
        with pytest.raises(InvalidRequest): await tools.inspect_admission()


@pytest.mark.parametrize('name,args', [
    ('search', {'query': ' '}), ('search', {'query': 'é' * 129}),
    ('search', {'query': 'a', 'limit': True}), ('search', {'query': 'a', 'limit': 6}),
    ('read', {'path': '../credential.txt'}), ('read', {'path': '/etc/passwd'}),
    ('read', {'path': 'x.py'}), ('read', {'path': 'a.md', 'start_line': False}),
    ('read', {'path': 'a.md', 'max_lines': 201}), ('read', {'path': 'a.md', 'url': 'https://x'}),
])
def test_operator_schemas_reject_broader_arguments(name, args):
    from hyperclaw.mcp import ARGUMENTS
    with pytest.raises(ValidationError): ARGUMENTS[name].model_validate(args)

@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [b'x' * 262145, b'{no}\n', b'{}', b'{"jsonrpc":"2.0","id":1,"method":"roots/list"}\n'], ids=["oversize", "malformed", "partial", "inbound"])
async def test_bounded_transport_rejects_bad_frames(payload):
    from hyperclaw.mcp_transport import byte_streams
    import asyncio
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    class Writer:
        def write(self, value): pass
        async def drain(self): pass
    async with byte_streams(reader, Writer()) as (read, write):
        result = await asyncio.wait_for(read.receive(), 1)
        assert isinstance(result, Exception)


def test_missing_sdk_is_clear_and_disabled_catalog_is_empty(tmp_path, monkeypatch):
    from hyperclaw.mcp import McpTools, require_sdk
    import builtins
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == 'mcp': raise ImportError('absent optional extra')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', blocked)
    assert McpTools(None, Settings(root=tmp_path), None).list_tools() == []
    with pytest.raises(InvalidRequest, match='optional'):
        require_sdk()


@pytest.mark.asyncio
async def test_manifest_configuration_and_catalog_changes_invalidate_admission(tmp_path, monkeypatch):
    import hyperclaw.mcp as module
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('text')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    async with opened(settings.root) as store:
        tools = module.McpTools(store, settings, None)
        preview = await tools.inspect_admission()
        await tools.admit(preview['sha256'])
        for changes in ({'mcp_docs_image': 'sha256:' + 'b'*64}, {'mcp_protocol': '2025-11-25'}):
            changed = module.McpTools(store, settings.model_copy(update=changes), None)
            assert (await changed.inspect_admission())['sha256'] != preview['sha256']
            with pytest.raises(Conflict): await changed.current()
        original = module.catalog()
        monkeypatch.setattr(module, 'catalog', lambda: original + [{'name': 'unreviewed'}])
        assert (await tools.inspect_admission())['sha256'] != preview['sha256']
        with pytest.raises(Conflict): await tools.current()


@pytest.mark.asyncio
async def test_snapshot_same_bytes_replacement_is_rejected(tmp_path):
    from hyperclaw.mcp import McpTools
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('text')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    async with opened(settings.root) as store:
        tools = McpTools(store, settings, None)
        preview = await tools.inspect_admission(); admitted = await tools.admit(preview['sha256'])
        snapshot = settings.root/'mcp-docs'/admitted['content_hash']
        snapshot.chmod(0o755)
        replacement = snapshot/'replacement.txt'; replacement.write_text('text'); replacement.chmod(0o444)
        replacement.replace(snapshot/'guide.md'); snapshot.chmod(0o555)
        with pytest.raises(Conflict): await tools.current()


@pytest.mark.asyncio
async def test_store_rejects_forged_manifest_policy_and_mutable_image(tmp_path):
    from hyperclaw.mcp import McpTools, digest
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('text')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    async with opened(settings.root) as store:
        tools = McpTools(store, settings, None)
        preview = await tools.inspect_admission()
        manifest = await tools.admit(preview['sha256'])
        manifest.pop('admission_id')
        for changes in ({'image': 'python:latest'}, {'policy': {'network': True}}, {'files': []}):
            forged = dict(manifest, **changes); forged.pop('sha256'); forged['sha256'] = digest({key: value for key, value in forged.items() if key != 'snapshot_identity'})
            with pytest.raises(InvalidRequest): await store.admit_mcp(forged)


@pytest.mark.parametrize('count,size,accepted', [(512, 1, True), (513, 1, False), (128, 65536, True), (129, 65536, False)])
def test_documentation_collection_exact_bounds(tmp_path, count, size, accepted):
    from hyperclaw.mcp import collect
    for index in range(count): (tmp_path/f'{index}.md').write_bytes(b'x'*size)
    if accepted:
        assert len(collect(tmp_path)) == count
    else:
        with pytest.raises(InvalidRequest): collect(tmp_path)


@pytest.mark.asyncio
async def test_revoke_then_readmit_cannot_restore_old_pending_authority(tmp_path):
    from hyperclaw.mcp import McpTools
    from hyperclaw.execution.policy import Policy
    from hyperclaw.contracts import ToolCall
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('text')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    async with opened(settings.root) as store:
        tools = McpTools(store, settings, None)
        preview = await tools.inspect_admission()
        first = await tools.admit(preview['sha256'])
        call = ToolCall(id='pending', name='mcp_docs_read', arguments={'path': 'guide.md'})
        old = Policy('workspace', [], first).check(call, ['mcp_docs_read'])
        await tools.revoke()
        second = await tools.admit(preview['sha256'])
        new = Policy('workspace', [], second).check(call, ['mcp_docs_read'])
        assert old.sha256 != new.sha256


@pytest.mark.asyncio
async def test_snapshot_rejects_added_nonpublic_file(tmp_path):
    from hyperclaw.mcp import McpTools
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('text')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    async with opened(settings.root) as store:
        tools = McpTools(store, settings, None)
        preview = await tools.inspect_admission(); manifest = await tools.admit(preview['sha256'])
        snapshot = settings.root/'mcp-docs'/manifest['content_hash']
        snapshot.chmod(0o755); (snapshot/'credentials.bin').write_text('outside admitted text')
        snapshot.chmod(0o555)
        with pytest.raises((Conflict, InvalidRequest)): await tools.current()
        with pytest.raises((Conflict, InvalidRequest)): await tools.admit(preview['sha256'])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,terminated,expected,code,calls', [
    ('catalog', True, 'failed', 'mcp_catalog_changed', 0),
    ('input_required', True, 'failed', 'mcp_input_required_unsupported', 1),
    ('image', True, 'failed', 'invalid_mcp_result', 1),
    ('oversize_result', True, 'failed', 'invalid_mcp_result', 1),
    ('stderr', False, 'uncertain', None, 1),
])
async def test_executor_sdk_failures_require_termination_and_never_replay(tmp_path, mode, terminated, expected, code, calls):
    import asyncio
    import json
    import sys
    from hyperclaw.execution import Executor
    from hyperclaw.execution.docker import DockerResult
    from hyperclaw.contracts import ToolCall
    from tests.integration.test_mcp import ROOT
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('text')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=IMAGE)
    wire = []
    class Backend:
        process = None
        async def create_docs(self, *args): return 'c' * 64
        async def verify_docs(self, *args): return {}
        async def attach_start(self, cid):
            self.process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT/'tests/support/mcp_peer.py'), mode,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            original = self.process.stdin
            class Recording:
                def write(self, data): wire.append(json.loads(data)); original.write(data)
                async def drain(self): await original.drain()
                def close(self): original.close()
            self.process.stdin = Recording()
            return self.process
        async def terminate(self, cid, output_limit=0):
            if self.process and self.process.returncode is None:
                self.process.kill(); await self.process.wait()
            return DockerResult('failed', terminated=terminated)
        async def remove(self, cid): pass
    async with opened(settings.root) as store:
        executor = await Executor.open(store, settings)
        executor.backend = executor.mcp.backend = Backend()
        try:
            preview = await executor.mcp.inspect_admission()
            manifest = await executor.mcp.admit(preview['sha256'])
            session = await store.create_session()
            run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id='test', text='docs', tools=['mcp_docs_search']), mcp=manifest)
            await store.next_run()
            call = ToolCall(id='search', name='mcp_docs_search', arguments={'query': 'text'})
            receipt = await executor.invoke(run.id, call)
            assert receipt.status == expected, receipt
            assert receipt.evidence['terminated'] is terminated
            if code: assert receipt.evidence['code'] == code
            assert await executor.invoke(run.id, call) == receipt
            assert sum(item.get('method') == 'tools/call' for item in wire) == calls
        finally:
            await executor.close()


@pytest.mark.asyncio
async def test_docs_container_creation_never_implicitly_pulls(tmp_path):
    from hyperclaw.execution.docker import DockerBackend, _CommandResult
    commands = []
    class RecordingBackend(DockerBackend):
        async def _run(self, args, timeout_s=15):
            commands.append(args)
            return _CommandResult(b'c'*64, b'', False)
    backend = RecordingBackend('test-installation', tmp_path)
    assert await backend.create_docs('invocation', tmp_path, IMAGE) == 'c'*64
    command = commands[0]
    assert command[command.index('--pull') + 1] == 'never'
