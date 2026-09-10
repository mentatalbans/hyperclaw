import threading
import time

import httpx
import pytest

from tests.support.process import Process, events, submit
from tests.support.provider import ProviderStub, Reply
from tests.support.tool_provider import frames, message_end, message_start, tool_block


@pytest.fixture
def service(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / "runtime", peer.url)
    try:
        app.start()
        yield app, peer
    finally:
        app.stop()
        peer.close()


def test_public_shell_is_the_only_unauthenticated_web_surface(service):
    app, peer = service
    token = (app.root / "token").read_text().strip()
    private_marker = "private-runtime-marker"
    (app.root / private_marker).write_text("private")

    with httpx.Client(base_url=app.url, trust_env=False) as anonymous:
        shell = anonymous.get("/")
        assert shell.status_code == 200
        assert token not in shell.text and private_marker not in shell.text
        assert 'id="token"' in shell.text
        assert "<script src=\"/web/app.js\" defer></script>" in shell.text
        for path, media_type in (
            ("/web/app.js", "text/javascript"),
            ("/web/style.css", "text/css"),
        ):
            response = anonymous.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(media_type)
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert token not in response.text
            assert private_marker not in response.text
            head = anonymous.head(path)
            assert head.status_code == 200 and head.content == b""

        assert shell.headers["content-security-policy"] == (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'"
        )
        assert shell.headers["cache-control"] == "no-store"
        assert anonymous.head("/").status_code == 200
        assert anonymous.get("/v1/sessions").status_code == 401
        assert anonymous.get(
            "/v1/sessions", headers={"Authorization": "Bearer forged"}
        ).status_code == 401
        assert anonymous.get("/v1/approvals").status_code == 401
        assert anonymous.post("/", json={}).status_code == 401
        assert anonymous.get("/web/missing.js").status_code == 401
        assert anonymous.get("/", headers={"Host": "evil.example"}).status_code == 400
        assert anonymous.get("/", headers={"Origin": "https://evil.example"}).status_code == 403
        assert anonymous.get("/v1/sessions", headers={
            "Authorization": f"Bearer {token}", "Origin": "https://evil.example",
        }).status_code == 403
        assert anonymous.post("/v1/sessions", headers={
            "Authorization": f"Bearer {token}", "Host": "evil.example",
        }).status_code == 400

    assert app.client.get("/web/missing.js").status_code == 404
    assert app.client.post("/v1/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
    assert peer.requests.empty()


def test_session_and_run_pages_are_bounded_stable_and_cursor_scoped(service):
    app, peer = service
    sessions = [app.client.post("/v1/sessions").json() for _ in range(4)]

    first_page = app.client.get("/v1/sessions", params={"limit": 2})
    assert first_page.status_code == 200
    assert first_page.json() == list(reversed(sessions[2:]))
    older = app.client.get(
        "/v1/sessions", params={"limit": 2, "before": first_page.json()[-1]["id"]}
    )
    assert older.json() == list(reversed(sessions[:2]))
    assert app.client.get("/v1/sessions", params={"before": "missing"}).status_code == 409
    for limit in (0, 101):
        assert app.client.get("/v1/sessions", params={"limit": limit}).status_code == 422

    peer.enqueue(Reply(chunks=("generation zero",)), Reply(chunks=("generation one",)), Reply())
    old_run = submit(app, "old", sessions[0], request_id="old")
    assert events(app, old_run["id"])[-1]["data"]["status"] == "succeeded"
    reset = app.client.post(
        f'/v1/sessions/{sessions[0]["id"]}/reset', json={"generation": 0}
    ).json()
    new_run = submit(app, "new", reset, request_id="new")
    assert events(app, new_run["id"])[-1]["data"]["status"] == "succeeded"
    other_run = submit(app, "other", sessions[1], request_id="other")
    assert events(app, other_run["id"])[-1]["data"]["status"] == "succeeded"

    path = f'/v1/sessions/{sessions[0]["id"]}/runs'
    assert app.client.get(path).json() == [
        app.client.get(f'/v1/runs/{new_run["id"]}').json(),
        app.client.get(f'/v1/runs/{old_run["id"]}').json(),
    ]
    assert app.client.get(path, params={"generation": 0}).json() == [
        app.client.get(f'/v1/runs/{old_run["id"]}').json()
    ]
    assert app.client.get(path, params={"generation": 1}).json() == [
        app.client.get(f'/v1/runs/{new_run["id"]}').json()
    ]
    assert app.client.get(path, params={"limit": 1}).json()[0]["id"] == new_run["id"]
    assert app.client.get(path, params={"limit": 1, "before": new_run["id"]}).json()[0]["id"] == old_run["id"]
    assert app.client.get(path, params={"before": other_run["id"]}).status_code == 409
    assert app.client.get(path, params={"generation": 0, "before": new_run["id"]}).status_code == 409
    assert app.client.get(path, params={"generation": -1}).status_code == 422
    for limit in (0, 101):
        assert app.client.get(path, params={"limit": limit}).status_code == 422

    assert app.client.get(f'/v1/runs/{old_run["id"]}').json()["output"] == "generation zero"
    assert [peer.take_request()["messages"][-1]["content"] for _ in range(3)] == [
        "old", "new", "other"
    ]


def test_web_read_and_mutation_boundaries_preserve_exact_approval_binding(service):
    app, peer = service
    peer.enqueue(
        Reply(frames=frames(
            message_start(),
            *tool_block(0, "hostile-call", "workspace_write", (
                '{"path":"<img src=x onerror=alert(1)>.txt","content":"safe"}',
            )),
            *message_end(),
        )),
        Reply(chunks=("Finished safely.",)),
    )
    run = submit(app, "render <script>alert(1)</script>")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        approvals = app.client.get("/v1/approvals").json()
        if approvals:
            break
        time.sleep(0.01)
    approval = approvals[0]
    assert approval["call"] == {
        "id": "hostile-call",
        "name": "workspace_write",
        "arguments": {"path": "<img src=x onerror=alert(1)>.txt", "content": "safe"},
    }

    decision_path = f'/v1/approvals/{approval["id"]}/decision'
    stale = app.client.post(decision_path, json={
        "approved": True,
        "arguments_sha256": "0" * 64,
        "policy_sha256": approval["policy_sha256"],
    })
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "approval_changed"
    assert app.client.get("/v1/approvals").json() == [approval]
    accepted = app.client.post(decision_path, json={
        "approved": True,
        "arguments_sha256": approval["arguments_sha256"],
        "policy_sha256": approval["policy_sha256"],
    })
    assert accepted.status_code == 200
    assert events(app, run["id"])[-1]["data"]["status"] == "succeeded"

    with httpx.Client(base_url=app.url, trust_env=False) as anonymous:
        assert anonymous.get(f'/v1/runs/{run["id"]}').status_code == 401
        assert anonymous.get(f'/v1/runs/{run["id"]}/events').status_code == 401
        assert anonymous.post(decision_path, json={}).status_code == 401
        assert anonymous.post(f'/v1/runs/{run["id"]}/cancel').status_code == 401
