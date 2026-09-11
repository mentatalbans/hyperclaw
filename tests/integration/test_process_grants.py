"""The public grant observer must read real capabilities, including mutations."""
from tests.support.process import Process, workspace_grants
from tests.support.provider import ProviderStub


def test_workspace_grant_observer_sees_empty_write_and_persisted_baselines(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url)
    try:
        app.start()
        assert workspace_grants(app) == []
        workspace = app.client.get('/v1/workspace')
        workspace.raise_for_status()
        granted = app.client.post('/v1/grants', json={
            'workspace_id': workspace.json()['id'], 'capability': 'write',
        })
        granted.raise_for_status()
        assert workspace_grants(app) == ['write']
        app.restart()
        assert workspace_grants(app) == ['write']
        assert peer.requests.empty()
    finally:
        app.stop()
        peer.close()
