"""Real SDK wire peers; Docker cases are explicitly selected."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def peer_transport(source, wire):
    from hyperclaw.mcp_transport import byte_streams
    process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'examples/mcp-docs/server.py'), str(source),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    class Recording:
        def write(self, data):
            wire.append(json.loads(data))
            process.stdin.write(data)
        async def drain(self): await process.stdin.drain()
    try:
        async with byte_streams(process.stdout, Recording(), process.stderr) as streams:
            yield streams
    finally:
        process.stdin.close()
        if process.returncode is None: process.kill()
        await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,revision', [('auto', '2026-07-28'), ('legacy', '2025-11-25')])
async def test_sdk_peer_real_wire_revisions(tmp_path, mode, revision):
    from mcp import Client
    from hyperclaw.mcp import catalog, validate_result
    (tmp_path / 'guide.md').write_text('opaque-doc-marker\nsecond\n')
    wire = []
    async with Client(peer_transport(tmp_path, wire), mode=mode, cache=None,
                      input_required_max_rounds=0, read_timeout_seconds=3) as client:
        assert client.protocol_version == revision
        listed = await client.list_tools()
        assert [{'name': t.name, 'description': t.description, 'inputSchema': t.input_schema,
                 'outputSchema': t.output_schema} for t in listed.tools] == catalog()
        result = await client.call_tool('read', {'path': 'guide.md'})
        assert 'opaque-doc-marker' in result.content[0].text
        value = validate_result(result, 'read', {'path': 'guide.md', 'start_line': 1, 'max_lines': 80})
        assert value['path'] == 'guide.md'
    methods = [item.get('method') for item in wire]
    assert methods.count('tools/call') == 1
    if mode == 'auto':
        assert 'server/discover' in methods and 'initialize' not in methods
        assert all(item['params']['_meta']['io.modelcontextprotocol/protocolVersion'] == revision
                   for item in wire if item.get('method') == 'tools/call')
    else:
        assert 'initialize' in methods and 'notifications/initialized' in methods
    output = ROOT / 'test-results/m5-task2'
    output.mkdir(parents=True, exist_ok=True)
    (output / f'wire-{mode}.json').write_text(json.dumps({'protocol': revision, 'wire': wire}, indent=2))


def configured_service(tmp_path, image='sha256:' + 'a' * 64, **kwargs):
    from tests.support.process import Process
    from tests.support.provider import ProviderStub
    source = tmp_path / 'public'
    source.mkdir()
    (source / 'guide.md').write_text('opaque-doc-marker\nRuntime uses SQLite for durable receipts.\n')
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, timeout=30, **kwargs)
    config = app.root / 'config.toml'
    value = config.read_text().replace('mcp_docs_path = ""', 'mcp_docs_path = ' + json.dumps(str(source)))
    config.write_text(value.replace('mcp_docs_image = ""', 'mcp_docs_image = ' + json.dumps(image)))
    return app, peer, source


def test_authenticated_http_cli_admission_and_revocation(tmp_path):
    import httpx
    from tests.integration.test_skills import cli
    app, peer, source = configured_service(tmp_path)
    try:
        app.start()
        with httpx.Client(base_url=app.url, trust_env=False) as stranger:
            assert stranger.get('/v1/mcp').status_code == 401
        preview = app.client.get('/v1/mcp')
        assert preview.status_code == 200
        assert app.client.post('/v1/mcp/admit', json={'expected_sha256': '0'*64}).status_code == 409
        inspected = cli(app, 'mcp', 'inspect')
        assert inspected.returncode == 0, inspected.stderr
        sha = json.loads(inspected.stdout)['sha256']
        assert cli(app, 'mcp', 'admit', '--expected-sha256', sha).returncode == 0
        assert cli(app, 'mcp', 'revoke').returncode == 0
        session = app.client.post('/v1/sessions').json()
        response = app.client.post('/v1/runs', json={'session_id': session['id'], 'generation': 0,
            'request_id': 'revoked', 'text': 'read docs', 'tools': ['mcp_docs_read']})
        assert response.status_code == 409
        assert peer.requests.empty()
    finally:
        app.stop(); peer.close()


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_docs_profile_and_actual_receipt(tmp_path, request):
    from hyperclaw.config import Settings
    from hyperclaw.contracts import RunRequest, ToolCall
    from hyperclaw.store import Store
    from hyperclaw.execution import Executor
    image = request.config.getoption('--mcp-docs-image')
    assert image, 'Build explicitly: docker build -f examples/mcp-docs/Dockerfile -t hyperclaw-mcp-docs .; pass --mcp-docs-image=$(docker image inspect -f "{{.Id}}" hyperclaw-mcp-docs)'
    source = tmp_path / 'public'; source.mkdir(); (source/'guide.md').write_text('opaque-doc-marker\n')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=image)
    store = await Store.open(settings.root)
    executor = await Executor.open(store, settings)
    try:
        manifest = await executor.mcp.inspect_admission()
        manifest = await executor.mcp.admit(manifest['sha256'])
        session = await store.create_session()
        run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id='docs', text='read', tools=['mcp_docs_read']), mcp=manifest)
        await store.next_run()
        receipt = await executor.invoke(run.id, ToolCall(id='read-1', name='mcp_docs_read', arguments={'path': 'guide.md'}))
        assert receipt.status == 'succeeded', receipt
        assert 'opaque-doc-marker' in receipt.output
        assert receipt.evidence['terminated'] is True
        assert receipt.evidence['content_hash'] == manifest['content_hash']
        assert receipt.evidence['profile']['init'] is True
        assert receipt.evidence['profile']['host'] == manifest['policy']['docker']['host']
        evidence_path = ROOT/'test-results/m5-task2/docker-receipt.json'
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps({'manifest': manifest, 'receipt': receipt.model_dump(mode='json')}, indent=2))
        assert not await executor.backend.owned()
        assert await executor.invoke(run.id, ToolCall(id='read-1', name='mcp_docs_read', arguments={'path': 'guide.md'})) == receipt
    finally:
        await executor.close(); await store.close()


@asynccontextmanager
async def hostile_transport(mode, wire, evidence):
    from hyperclaw.mcp_transport import byte_streams
    process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT/'tests/support/mcp_peer.py'), mode,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    class Recording:
        def write(self, data):
            wire.append(json.loads(data)); process.stdin.write(data)
        async def drain(self): await process.stdin.drain()
    try:
        async with byte_streams(process.stdout, Recording(), process.stderr, evidence=evidence) as streams:
            yield streams
    finally:
        process.stdin.close()
        if process.returncode is None: process.kill()
        await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['input_required', 'timeout', 'disconnect', 'oversize_frame', 'malformed', 'inbound', 'image', 'oversize_result'])
async def test_sdk_adverse_peers_are_bounded_and_never_retried(mode):
    from mcp import Client
    from hyperclaw.mcp import validate_result
    wire, evidence = [], {}
    started = asyncio.get_running_loop().time()
    with pytest.raises(Exception):
        async with asyncio.timeout(3):
            async with Client(hostile_transport(mode, wire, evidence), mode='auto', cache=None,
                              input_required_max_rounds=0, read_timeout_seconds=.3) as client:
                result = await client.call_tool('search', {'query': 'test', 'limit': 5})
                validate_result(result, 'search', {'query': 'test', 'limit': 5})
    assert [item.get('method') for item in wire].count('tools/call') == 1
    assert asyncio.get_running_loop().time() - started < 3


@pytest.mark.asyncio
async def test_sdk_stderr_flood_is_drained_and_capped():
    from mcp import Client
    from hyperclaw.mcp import validate_result
    wire, evidence = [], {}
    async with Client(hostile_transport('stderr', wire, evidence), mode='auto', cache=None,
                      input_required_max_rounds=0, read_timeout_seconds=2) as client:
        result = await client.call_tool('search', {'query': 'test', 'limit': 5})
        assert validate_result(result, 'search', {'query': 'test', 'limit': 5}) == {'hits': []}
    assert evidence['stderr_bytes'] == 65536


@pytest.mark.asyncio
async def test_sdk_cancellation_closes_transport_without_reissue():
    from mcp import Client
    wire, evidence = [], {}
    async def call():
        async with Client(hostile_transport('timeout', wire, evidence), mode='auto', cache=None,
                          input_required_max_rounds=0, read_timeout_seconds=10) as client:
            await client.call_tool('search', {'query': 'test'})
    task = asyncio.create_task(call())
    for _ in range(200):
        if any(item.get('method') == 'tools/call' for item in wire): break
        await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert [item.get('method') for item in wire].count('tools/call') == 1


def mcp_reply(*calls):
    from tests.support.provider import Reply
    from tests.support.tool_provider import frames, message_end, message_start, tool_block
    blocks = []
    for i, (name, args) in enumerate(calls):
        blocks.extend(tool_block(i, f'docs-{i}', name, (json.dumps(args),)))
    return Reply(frames=frames(message_start(), *blocks, *message_end()))


@pytest.mark.docker
def test_http_selected_read_search_grouped_sources_and_no_escalation(tmp_path, request):
    from tests.support.process import events, submit, workspace_grants
    from tests.support.provider import Reply
    from tests.live.test_recovery_docker import cleanup_owned, owned_container_ids
    image = request.config.getoption('--mcp-docs-image')
    assert image, 'Build examples/mcp-docs/Dockerfile explicitly and pass --mcp-docs-image with its inspected sha256 ID.'
    app, peer, source = configured_service(tmp_path, image)
    try:
        app.start()
        manifest = app.client.get('/v1/mcp').json()
        app.client.post('/v1/mcp/admit', json={'expected_sha256': manifest['sha256']}).raise_for_status()
        grants = workspace_grants(app)
        assert grants == []
        peer.enqueue(mcp_reply(('mcp_docs_search', {'query': 'SQLite'}), ('mcp_docs_read', {'path': 'guide.md'})), Reply(chunks=('SQLite persists receipts (guide.md:2). opaque-doc-marker',)))
        run = submit(app, 'Read docs', tools=['mcp_docs_search', 'mcp_docs_read'])
        assert events(app, run['id'])[-1]['data']['status'] == 'succeeded'
        saved = app.client.get(f"/v1/runs/{run['id']}").json()
        assert saved['mcp']['sha256'] == manifest['sha256']
        assert saved['mcp']['content_hash'] == manifest['content_hash']
        assert saved['mcp']['admission_id']
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert len(receipts) == 2 and all(r['status'] == 'succeeded' for r in receipts)
        assert all(r['evidence']['sources'][0]['sha256'] == manifest['files'][0]['sha256'] for r in receipts)
        first, second = peer.take_request(), peer.take_request()
        results = second['messages'][-1]['content']
        assert len(results) == 2 and all(r['type'] == 'tool_result' for r in results)
        assert workspace_grants(app) == grants
        assert owned_container_ids(app.root) == []
        app.client.delete('/v1/mcp/admission').raise_for_status()
        app.restart()
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == receipts
    finally:
        app.stop(); cleanup_owned(app.root); peer.close()


@pytest.mark.docker
@pytest.mark.parametrize('stage', ['after_create', 'after_start', 'after_receipt'])
def test_mcp_sigkill_reconciles_twice_even_disabled_and_without_sdk(tmp_path, request, stage):
    from tests.support.process import events, submit
    from tests.live.test_recovery_docker import cleanup_owned, owned_container_ids, wait_for
    image = request.config.getoption('--mcp-docs-image')
    assert image, 'Build examples/mcp-docs/Dockerfile explicitly and pass --mcp-docs-image with its inspected sha256 ID.'
    launcher = ROOT / 'tests/support/mcp_recovery_daemon.py'
    app, peer, source = configured_service(tmp_path, image, launcher=launcher)
    (app.root/'mcp-test-fault.json').write_text(json.dumps({'stage': stage}))
    try:
        app.start()
        manifest = app.client.get('/v1/mcp').json()
        app.client.post('/v1/mcp/admit', json={'expected_sha256': manifest['sha256']}).raise_for_status()
        peer.enqueue(mcp_reply(('mcp_docs_read', {'path': 'guide.md'})))
        run = submit(app, tools=['mcp_docs_read'])
        wait_for(app.root/'mcp-test-barrier', app, seconds=30)
        assert owned_container_ids(app.root)
        app.kill()
        config = app.root/'config.toml'
        config.write_text(config.read_text().replace('mcp_docs_path = ' + json.dumps(str(source)), 'mcp_docs_path = ""').replace('mcp_docs_image = ' + json.dumps(image), 'mcp_docs_image = ""'))
        (app.root/'mcp-test-fault.json').write_text(json.dumps({'stage': 'missing_sdk'}))
        app.start()
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert len(receipts) == 1
        assert receipts[0]['status'] == ('succeeded' if stage == 'after_receipt' else 'interrupted')
        assert receipts[0]['evidence']['terminated'] is True
        assert owned_container_ids(app.root) == []
        original_events = events(app, run['id'])
        app.restart()
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == receipts
        assert events(app, run['id']) == original_events
        assert peer.requests.qsize() == 1
        assert owned_container_ids(app.root) == []
    finally:
        app.stop(); cleanup_owned(app.root); peer.close()


def test_selected_mcp_reports_missing_extra_when_sdk_absent(tmp_path):
    """Public base-wheel gate: run in an environment without hyperclaw[mcp]."""
    from importlib.util import find_spec
    if find_spec('mcp') is not None:
        pytest.skip('This public-path gate requires a genuinely base-only installation.')
    app, peer, source = configured_service(tmp_path)
    try:
        app.start()
        preview = app.client.get('/v1/mcp').json()
        app.client.post('/v1/mcp/admit', json={'expected_sha256': preview['sha256']}).raise_for_status()
        session = app.client.post('/v1/sessions').json()
        response = app.client.post('/v1/runs', json={'session_id': session['id'], 'generation': 0,
            'request_id': 'missing-sdk', 'text': 'read docs', 'tools': ['mcp_docs_read']})
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'mcp_unavailable'
        assert 'optional' in response.json()['error']['message']
        assert peer.requests.empty()
        assert app.client.get('/healthz').status_code == 200
    finally:
        app.stop(); peer.close()


def test_selected_mcp_rejects_reference_traversal_before_peer_start(tmp_path):
    from tests.support.process import events, submit
    app, peer, source = configured_service(tmp_path)
    sentinel = tmp_path/'credential.txt'; sentinel.write_text('credential-sentinel')
    try:
        app.start()
        preview = app.client.get('/v1/mcp').json()
        app.client.post('/v1/mcp/admit', json={'expected_sha256': preview['sha256']}).raise_for_status()
        peer.enqueue(mcp_reply(('mcp_docs_read', {'path': '../credential.txt'})))
        run = submit(app, tools=['mcp_docs_read'])
        assert events(app, run['id'])[-1]['data']['status'] == 'failed'
        saved = app.client.get(f"/v1/runs/{run['id']}").json()
        assert saved['error']['code'] == 'invalid_tool_arguments'
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == []
        assert sentinel.read_text() == 'credential-sentinel'
    finally:
        app.stop(); peer.close()


def test_revoke_readmit_blocks_checkpointed_approval(tmp_path):
    import time
    from tests.support.process import events, submit
    app, peer, source = configured_service(tmp_path)
    try:
        app.start()
        preview = app.client.get('/v1/mcp').json()
        admitted = app.client.post('/v1/mcp/admit', json={'expected_sha256': preview['sha256']}).json()
        peer.enqueue(mcp_reply(('workspace_write', {'path': 'must-not-exist.txt', 'content': 'unsafe'})))
        run = submit(app, tools=['workspace_write', 'mcp_docs_read'])
        for _ in range(400):
            approvals = app.client.get('/v1/approvals').json()
            if approvals: break
            time.sleep(.01)
        approval = approvals[0]
        app.client.delete('/v1/mcp/admission').raise_for_status()
        renewed = app.client.post('/v1/mcp/admit', json={'expected_sha256': preview['sha256']}).json()
        assert renewed['admission_id'] != admitted['admission_id']
        response = app.client.post(f"/v1/approvals/{approval['id']}/decision", json={
            'approved': True, 'arguments_sha256': approval['arguments_sha256'],
            'policy_sha256': approval['policy_sha256']})
        assert response.status_code == 409
        assert not (app.root/'workspace/must-not-exist.txt').exists()
        assert peer.requests.qsize() == 1
    finally:
        app.stop(); peer.close()


@pytest.mark.asyncio
async def test_sdk_unreviewed_catalog_fails_before_tools_call():
    from mcp import Client
    from hyperclaw.mcp import catalog, review_catalog
    from hyperclaw.contracts import Conflict
    wire, evidence = [], {}
    async with Client(hostile_transport('catalog', wire, evidence), mode='auto', cache=None,
                      input_required_max_rounds=0, read_timeout_seconds=2) as client:
        listed = await client.list_tools()
        with pytest.raises(Conflict): review_catalog(listed, catalog())
    assert not any(item.get('method') == 'tools/call' for item in wire)


@pytest.mark.docker
@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['malformed', 'oversize_frame', 'oversize_result', 'disconnect', 'stderr', 'timeout', 'cancel'])
async def test_real_docker_adverse_attachment_is_bounded_and_owned(tmp_path, request, mode):
    """The fault command is confined to this test backend, inside the real profile."""
    import hashlib
    from hyperclaw.config import Settings
    from hyperclaw.contracts import RunRequest, ToolCall
    from hyperclaw.execution import Executor
    from hyperclaw.execution.docker import DockerBackend
    from hyperclaw.store import Store
    image = request.config.getoption('--mcp-docs-image')
    assert image, 'Build examples/mcp-docs/Dockerfile explicitly and pass --mcp-docs-image with its inspected sha256 ID.'
    fixture = (ROOT/'tests/support/mcp_peer.py').read_text().replace(
        "if mode == 'timeout': await asyncio.sleep(20)",
        "if mode == 'timeout':\n        sys.stderr.write('test-call-started\\n'); sys.stderr.flush(); await asyncio.sleep(20)")
    peer_mode = 'timeout' if mode == 'cancel' else mode
    class FaultBackend(DockerBackend):
        async def _run(self, args, timeout_s=15):
            if args[0] == 'create' and args[-1] == '/opt/server.py':
                args = args[:-1] + ['-c', fixture, peer_mode]
            return await super()._run(args, timeout_s=timeout_s)
        async def verify_docs(self, cid, snapshot, expected_image):
            await self.inspect(cid)  # Production owner-label validation.
            raw = await self._inspect_raw(cid)
            config, host, mounts = raw['Config'], raw['HostConfig'], raw['Mounts']
            assert config['Image'] == expected_image
            assert config['User'] == '65532:65532'
            assert config['Entrypoint'] == ['/usr/local/bin/python']
            assert config['Cmd'] == ['-c', fixture, peer_mode]
            assert config['WorkingDir'] == '/docs' and config['OpenStdin']
            assert host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] and not host['Privileged']
            assert host['CapDrop'] == ['ALL'] and not host.get('CapAdd')
            assert 'no-new-privileges=true' in host['SecurityOpt']
            assert host['Init'] is True
            assert host['Memory'] == host['MemorySwap'] == 268435456
            assert host['PidsLimit'] == 64 and host['NanoCpus'] == 1000000000
            assert host['Tmpfs'] == {'/tmp': 'rw,noexec,nosuid,nodev,size=16m'}
            assert host['LogConfig'] == {'Type': 'local', 'Config': {'max-size': '1m', 'max-file': '1', 'compress': 'false'}}
            assert len(mounts) == 1 and mounts[0]['Destination'] == '/docs' and not mounts[0]['RW']
            assert Path(mounts[0]['Source']).resolve() == snapshot.resolve()
            assert {entry.split('=', 1)[0] for entry in config['Env']} <= {'PATH', 'LANG', 'GPG_KEY', 'PYTHON_VERSION', 'PYTHON_SHA256', 'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE'}
            return {'fixture_sha256': hashlib.sha256(fixture.encode()).hexdigest(),
                    'mounts': mounts, 'user': config['User'], 'network': host['NetworkMode'],
                    'readonly_root': host['ReadonlyRootfs'], 'memory': host['Memory'],
                    'pids': host['PidsLimit'], 'environment': config['Env']}
    source = tmp_path/'public'; source.mkdir(); (source/'guide.md').write_text('test docs')
    settings = Settings(root=tmp_path/'runtime', mcp_docs_path=str(source), mcp_docs_image=image,
                        run_timeout_s=4.0 if mode in {'timeout', 'malformed', 'oversize_frame'} else 30.0)
    store = await Store.open(settings.root)
    executor = await Executor.open(store, settings)
    backend = FaultBackend(await store.installation_id(), executor.backend.workspace)
    executor.backend = executor.mcp.backend = backend
    started = asyncio.get_running_loop().time()
    try:
        preview = await executor.mcp.inspect_admission()
        manifest = await executor.mcp.admit(preview['sha256'])
        session = await store.create_session()
        run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id=mode,
                                            text='test docs', tools=['mcp_docs_search']), mcp=manifest)
        await store.next_run()
        call = ToolCall(id='fault-call', name='mcp_docs_search', arguments={'query': 'test'})
        if mode == 'cancel':
            task = asyncio.create_task(executor.invoke(run.id, call))
            for _ in range(100):
                owned = await backend.owned()
                if owned and 'test-call-started' in (await backend._logs(owned[0]['id'], 65536))[0]: break
                await asyncio.sleep(.05)
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                pytest.fail('SDK peer did not reach tools/call before cancellation')
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 10)
            receipt = (await store.invocations(run.id))[0].receipt
        else:
            receipt = await executor.invoke(run.id, call)
        assert receipt is not None
        assert receipt.status == ('succeeded' if mode == 'stderr' else 'cancelled' if mode == 'cancel' else 'failed'), receipt
        assert receipt.evidence['terminated'] is True
        assert len(receipt.output.encode()) <= 65536
        assert await backend.owned() == []
        if mode == 'stderr': assert receipt.evidence['stderr_bytes'] == 65536
        assert asyncio.get_running_loop().time() - started < 20
        assert await executor.invoke(run.id, call) == receipt
        evidence_path = ROOT/f'test-results/m5-task2/docker-adverse-{mode}.json'
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(receipt.model_dump(mode='json'), indent=2))
    finally:
        for container in await backend.owned():
            await backend.terminate(container['id']); await backend.remove(container['id'])
        await executor.close(); await store.close()


def test_admitted_mcp_still_requires_direct_run_not_schedule(tmp_path):
    from tests.integration.test_scheduling import schedule_body
    app, peer, source = configured_service(tmp_path)
    try:
        app.start()
        preview = app.client.get('/v1/mcp').json()
        app.client.post('/v1/mcp/admit', json={'expected_sha256': preview['sha256']}).raise_for_status()
        session = app.client.post('/v1/sessions').json()
        response = app.client.post('/v1/schedules', json=schedule_body(session, tools=('mcp_docs_read',)))
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'mcp_schedule_unsupported'
        assert app.client.get('/healthz').status_code == 200
        assert peer.requests.empty()
    finally:
        app.stop(); peer.close()
