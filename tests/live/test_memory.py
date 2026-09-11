"""A real local model retrieves an opaque fact from a freshly opened Store."""
import asyncio
import json
from uuid import uuid4

import pytest

from tests.support.process import events, submit

pytestmark = pytest.mark.ollama


def test_live_memory_search_recovers_opaque_fact_after_store_reopen(live_app, request):
    # No model conversation contains the marker: seed through the public library
    # while the daemon is stopped, then reopen the Store in the real daemon.
    from hyperclaw.contracts import MemoryScope
    from hyperclaw.memory import Memory
    from hyperclaw.store import Store

    app = live_app
    session = app.client.post('/v1/sessions', json={}).json()
    workspace = app.client.get('/v1/workspace').json()
    marker = 'archive-' + uuid4().hex
    app.stop()

    async def seed():
        store = await Store.open(app.root)
        try:
            scope = MemoryScope(workspace_id=workspace['id'], session_id=session['id'])
            return await Memory(store).remember(scope, f'The synthetic archive calibration marker is {marker}.')
        finally:
            await store.close()

    record = asyncio.run(seed())
    app.start()
    prompt = ('Use memory_search with scope session and limit 5 to find this session\'s '
              'synthetic archive calibration marker. Answer with the exact stored marker. Do not guess.')
    assert marker not in prompt
    run = submit(app, prompt, session, tools=['memory_search'])
    received = events(app, run['id'])
    result = app.client.get(f"/v1/runs/{run['id']}").json()
    assert result['status'] == 'succeeded', result
    assert marker in result['output'], result
    receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
    matches = [fact for receipt in receipts if receipt['status'] == 'succeeded'
               for fact in json.loads(receipt['output']) if fact['id'] == record.id]
    assert matches and all(fact['text'] == record.text for fact in matches)
    assert all(fact['version'] == 1 and fact['source_run_id'] is None for fact in matches)
    assert all(fact['scope'] == {'workspace_id': workspace['id'], 'session_id': session['id']} for fact in matches)
    assert any(event['kind'] == 'tool.requested' for event in received)
    assert received[-1]['data']['status'] == 'succeeded'
    assert app.client.get('/v1/workspace').json()['grants'] == []
    request.node.user_properties.extend([
        ('memory_marker', marker), ('memory_record_id', record.id),
        ('memory_run_id', run['id']), ('memory_session_id', session['id']),
        ('memory_source_run_id', 'null (explicit library seed)'),
        ('memory_tool_receipts', len(receipts)),
    ])
