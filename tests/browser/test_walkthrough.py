"""Deterministic one-root public walkthrough with explicit browser and Docker gates."""

import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

from hyperclaw.contracts import canonical
from tests.browser.test_web import connect, send, wait_status
from tests.integration.test_memory import memory_call, search, tool_reply
from tests.integration.test_recovery import grant_write, wait_for
from tests.live.test_recovery_docker import cleanup_owned, owned_container_ids
from tests.support.process import Process, events, submit, workspace_grants
from tests.support.provider import ProviderStub, Reply


pytestmark = [pytest.mark.browser, pytest.mark.docker]
ROOT = Path(__file__).resolve().parents[2]


def configure(app, **values):
    assert app.process is None
    path = app.root / "config.toml"
    text = path.read_text()
    for key, value in values.items():
        text, count = re.subn(
            rf"^{re.escape(key)} = .*$", f"{key} = {json.dumps(value)}", text, flags=re.M
        )
        assert count == 1, key
    path.write_text(text)


def result(app, identifier):
    observed_events = events(app, identifier)
    return {
        "run": app.client.get(f"/v1/runs/{identifier}").json(),
        "receipts": app.client.get(f"/v1/runs/{identifier}/receipts").json(),
        "events": observed_events,
    }


def test_one_root_walkthrough_survives_recovery_without_repeating_effects(
    tmp_path, browser_page, request
):
    image = request.config.getoption("--mcp-docs-image")
    assert image, (
        "Build examples/mcp-docs/Dockerfile explicitly and pass --mcp-docs-image "
        "with its inspected sha256 ID."
    )
    peer = ProviderStub()
    app = Process(tmp_path / "runtime", peer.url, timeout=30)
    page = browser_page
    browser_errors = []
    page.on("pageerror", lambda error: browser_errors.append(str(error)))
    page.on(
        "console",
        lambda message: browser_errors.append(message.text) if message.type == "error" else None,
    )
    docs = tmp_path / "public-docs"
    docs.mkdir()
    doc_marker = "walkthrough-doc-7c443f"
    source = f"# Synthetic walkthrough documentation\n\nThe exhibit marker is {doc_marker}.\n"
    (docs / "README.md").write_text(source)
    configure(app, mcp_docs_path=str(docs), mcp_docs_image=image)
    shutil.copytree(
        ROOT / "examples/skills/documentation-answer",
        app.root / "skills/documentation-answer",
    )
    try:
        app.start()
        connect(page, app)
        page.locator("#new-session").click()
        page.locator("#session-id").filter(has_not_text="None").wait_for()
        peer.enqueue(Reply(chunks=("Hello from the deterministic walkthrough.",)))
        send(page, "Start the deterministic walkthrough.")
        wait_status(page, "succeeded")
        chat_id = page.locator("#run-id").text_content()
        chat = result(app, chat_id)
        session = app.client.get(
            "/v1/sessions/" + chat["run"]["request"]["session_id"]
        ).json()
        assert chat["run"]["status"] == "succeeded" and chat["receipts"] == []
        assert sum(event["kind"] == "run.finished" for event in chat["events"]) == 1
        assert peer.take_request()["messages"][-1]["content"] == "Start the deterministic walkthrough."
        assert peer.requests.empty()

        marker = "verified-file-426d9b"
        arguments = {"path": "verified.txt", "content": marker}
        peer.enqueue(
            tool_reply("workspace_write", arguments, call_id="walkthrough-write"),
            Reply(chunks=("The synthetic file was written and verified.",)),
        )
        send(page, "Write the synthetic verification file.")
        approval_card = page.locator("#approvals article").first
        approval_card.wait_for()
        pending, = app.client.get("/v1/approvals").json()
        assert pending["call"] == {
            "id": "walkthrough-write", "name": "workspace_write", "arguments": arguments,
        }
        assert pending["arguments_sha256"] == hashlib.sha256(
            canonical(arguments).encode()
        ).hexdigest()
        assert marker in approval_card.text_content()
        approval_card.locator('button[data-decision="approve"]').click()
        wait_status(page, "succeeded")
        write_id = page.locator("#run-id").text_content()
        write = result(app, write_id)
        expected_file_hash = hashlib.sha256(marker.encode()).hexdigest()
        assert write["run"]["status"] == "succeeded"
        assert write["run"]["verification"] == "passed"
        assert len(write["receipts"]) == 1
        assert write["receipts"][0]["artifacts"] == [{
            "path": "verified.txt", "sha256": expected_file_hash, "size_bytes": 20,
        }]
        assert hashlib.sha256((app.root / "workspace/verified.txt").read_bytes()).hexdigest() == expected_file_hash
        assert sum(event["kind"] == "tool.finished" for event in write["events"]) == 1
        peer.take_request()
        peer.take_request()
        assert peer.requests.empty()
        screenshot_dir = Path(request.config.getoption("--browser-screenshot-dir"))
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / "walkthrough-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(screenshot_dir / "walkthrough-mobile.png"), full_page=True)
        page.locator("#logout").click()
        page.locator("#workspace").wait_for(state="hidden")
        assert page.locator("#token").input_value() == ""

        app.stop()
        app.launcher = ROOT / "tests/support/recovery_daemon.py"
        (app.root / "test-fault.json").write_text(json.dumps({"stage": "after_receipt"}))
        app.start()
        grant_write(app)
        stable_grants = workspace_grants(app)
        assert stable_grants == ["write"]
        peer.enqueue(
            tool_reply(
                "workspace_write",
                {"path": "recovered.txt", "content": "committed once"},
                call_id="walkthrough-recovery-write",
            )
        )
        interrupted = submit(
            app, "Synthetic interrupted file task.", session, "walkthrough-interrupted",
            tools=["workspace_write"],
        )
        wait_for(lambda: (app.root / "test-barrier").exists(), app)
        assert (app.root / "workspace/recovered.txt").read_text() == "committed once"
        app.kill()
        app.launcher = None
        operator_text = "operator edit after kill"
        (app.root / "workspace/recovered.txt").write_text(operator_text)
        stable_receipts = None
        stable_events = None
        for _ in range(2):
            app.start()
            recovered = result(app, interrupted["id"])
            assert recovered["run"]["status"] == "interrupted"
            assert recovered["run"]["verification"] == "passed"
            assert len(recovered["receipts"]) == 1
            assert recovered["receipts"][0]["status"] == "succeeded"
            assert sum(event["kind"] == "tool.finished" for event in recovered["events"]) == 1
            assert hashlib.sha256((app.root / "workspace/recovered.txt").read_bytes()).hexdigest() == hashlib.sha256(operator_text.encode()).hexdigest()
            duplicate = app.client.post("/v1/runs", json=interrupted["request"])
            assert duplicate.status_code == 202
            assert duplicate.json()["id"] == interrupted["id"]
            if stable_receipts is None:
                stable_receipts, stable_events = recovered["receipts"], recovered["events"]
            else:
                assert recovered["receipts"] == stable_receipts
                assert recovered["events"] == stable_events
            assert workspace_grants(app) == stable_grants
            app.stop()
        peer.take_request()
        assert peer.requests.empty()

        app.start()
        _, remembered_receipt = memory_call(
            app, peer, session, "memory_remember", {"text": "The exhibit label is amber-old."}
        )
        old = json.loads(remembered_receipt["output"])
        _, corrected_receipt = memory_call(
            app, peer, session, "memory_correct",
            {"record_id": old["id"], "text": "The exhibit label is indigo-current."},
        )
        current = json.loads(corrected_receipt["output"])
        app.restart()
        assert search(app, peer, session, "amber-old") == []
        assert search(app, peer, session, "exhibit label") == [current]
        assert current["supersedes"] == old["id"] and current["version"] == 2
        assert workspace_grants(app) == stable_grants

        skill = app.client.get("/v1/skills/documentation-answer").json()
        app.client.post(
            "/v1/skills/documentation-answer/admit", json={"content_hash": skill["content_hash"]}
        ).raise_for_status()
        manifest = app.client.get("/v1/mcp").json()
        app.client.post(
            "/v1/mcp/admit", json={"expected_sha256": manifest["sha256"]}
        ).raise_for_status()
        peer.enqueue(
            tool_reply(
                "mcp_docs_read", {"path": "README.md", "start_line": 1, "max_lines": 10},
                call_id="walkthrough-docs-read",
            ),
            Reply(chunks=("The synthetic documentation marker was retrieved.",)),
        )
        lookup = submit(
            app, "Read the reviewed synthetic documentation.", session, "walkthrough-docs",
            tools=["mcp_docs_read"], skills=["documentation-answer"],
        )
        lookup_evidence = result(app, lookup["id"])
        assert lookup_evidence["run"]["status"] == "succeeded"
        assert lookup_evidence["run"]["skill_hashes"] == {
            "documentation-answer": skill["content_hash"]
        }
        assert len(lookup_evidence["receipts"]) == 1
        receipt = lookup_evidence["receipts"][0]
        assert doc_marker in receipt["output"] and receipt["evidence"]["terminated"] is True
        assert any(
            item["sha256"] == hashlib.sha256(source.encode()).hexdigest()
            for item in receipt["evidence"]["sources"]
        )
        assert sum(event["kind"] == "tool.finished" for event in lookup_evidence["events"]) == 1
        provider_request = peer.take_request()
        assert skill["name"] in provider_request["system"]
        peer.take_request()
        assert peer.requests.empty()
        assert workspace_grants(app) == stable_grants
        assert owned_container_ids(app.root) == []
        assert browser_errors == []
    finally:
        app.stop()
        cleanup_owned(app.root)
        peer.close()
        assert owned_container_ids(app.root) == []
