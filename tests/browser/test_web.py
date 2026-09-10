import json
from pathlib import Path
import threading

import pytest

from hyperclaw.skills import Skills
from tests.support.process import Process, events, submit
from tests.support.provider import ProviderStub, Reply
from tests.support.tool_provider import frames, message_end, message_start, tool_block


pytestmark = pytest.mark.browser


@pytest.fixture
def web_service(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / "runtime", peer.url)
    try:
        app.start()
        yield app, peer
    finally:
        app.stop()
        peer.close()


def connect(page, app):
    page.goto(app.url)
    page.locator("#token").fill((app.root / "token").read_text().strip())
    page.locator("#connect").click()
    page.locator("#workspace").wait_for(state="visible")
    page.locator("#connection-status").filter(has_text="Connected").wait_for()


def send(page, text):
    page.locator("#message").fill(text)
    page.locator("#send").click()


def wait_status(page, status):
    page.locator("#run-status").filter(has_text=status).wait_for()


def wait_approval(page):
    approval = page.locator("#approvals article").first
    approval.wait_for()
    return approval


def gate_fetch_completion(page, key, method, path):
    page.evaluate(
        """({key, method, path}) => {
          if (!window.__fetchGates) {
            window.__fetchGates = new Map();
            window.__ungatedFetch = window.fetch.bind(window);
            window.fetch = async (input, options = {}) => {
              const request = input instanceof Request ? input : null;
              const url = new URL(request ? request.url : input, location.href);
              const verb = (options.method || (request && request.method) || 'GET').toUpperCase();
              const response = await window.__ungatedFetch(input, options);
              for (const gate of window.__fetchGates.values()) {
                if (!gate.claimed && gate.method === verb && gate.path === url.pathname) {
                  gate.claimed = true;
                  gate.seen = true;
                  await new Promise(resolve => { gate.release = resolve; });
                  gate.done = true;
                  break;
                }
              }
              return response;
            };
          }
          window.__fetchGates.set(key, {method, path, claimed: false, seen: false, done: false});
        }""",
        {"key": key, "method": method, "path": path},
    )


def wait_fetch_gate(page, key):
    page.wait_for_function("key => window.__fetchGates.get(key).seen", arg=key)


def release_fetch_gate(page, key):
    page.evaluate(
        """async key => {
          window.__fetchGates.get(key).release();
          await window.__ungatedFetch('/healthz', {cache: 'no-store'});
          await new Promise(resolve => requestAnimationFrame(() => resolve()));
        }""",
        key,
    )


def test_connect_reload_stream_logout_and_hostile_text_are_safe(web_service, browser_page, request):
    app, peer = web_service
    page = browser_page
    requested_urls = []
    page.on("request", lambda outgoing: requested_urls.append(outgoing.url))
    skill_dir = app.root / "skills" / "browser-guidance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: browser-guidance\ndescription: Browser fixture guidance.\n---\nUse synthetic text.\n"
    )
    skill = Skills(app.root / "skills").load("browser-guidance")
    app.client.post(
        "/v1/skills/browser-guidance/admit", json={"content_hash": skill.content_hash}
    ).raise_for_status()
    gate = threading.Event()
    peer.enqueue(Reply(chunks=("Synthetic <img src=x onerror=alert(1)> ", "snowman ☃"), gate=gate))

    connect(page, app)
    assert page.locator("#token").input_value() == ""
    assert page.locator('input[name="tools"]:checked').count() == 9
    assert page.locator('input[name="tools"][value^="mcp_"]:checked').count() == 0
    assert page.locator('input[name="skills"][value="browser-guidance"]').count() == 1
    page.locator("details summary").click()
    page.locator('input[name="skills"][value="browser-guidance"]').check()
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, "hostile user <script>window.pwned=true</script>")
    accepted = peer.take_request()
    page.locator("#transcript").filter(has_text="<img src=x").wait_for()
    assert page.locator("#transcript img").count() == 0
    assert page.evaluate("window.pwned") is None
    assert accepted["messages"][-1]["content"] == "hostile user <script>window.pwned=true</script>"
    assert "Use synthetic text." in accepted["system"]

    current_hash = page.evaluate("location.hash")
    page.reload()
    page.locator("#token").fill((app.root / "token").read_text().strip())
    page.locator("#connect").click()
    page.locator("#workspace").wait_for(state="visible")
    assert page.evaluate("location.hash") == current_hash
    gate.set()
    wait_status(page, "succeeded")
    assert peer.requests.empty(), "reload/reconnect must not submit a second provider request"
    assert "snowman ☃" in page.locator("#transcript").text_content()
    assert page.locator("#transcript img").count() == 0

    storage = page.evaluate("""async () => ({
      local: Object.keys(localStorage), session: Object.keys(sessionStorage),
      databases: indexedDB.databases ? await indexedDB.databases() : [],
      cookies: document.cookie
    })""")
    assert storage == {"local": [], "session": [], "databases": [], "cookies": ""}
    assert all((app.root / "token").read_text().strip() not in url for url in requested_urls)
    screenshot_dir = Path(request.config.getoption("--browser-screenshot-dir"))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    hostile_screenshot = screenshot_dir / "hostile-content.png"
    page.screenshot(path=str(hostile_screenshot), full_page=True)
    assert hostile_screenshot.is_file() and hostile_screenshot.stat().st_size > 0

    peer.enqueue(Reply(chunks=("A calm synthetic answer.",)))
    prior_run_id = page.locator("#run-id").text_content()
    send(page, "Show a normal synthetic conversation")
    page.locator("#run-id").filter(has_not_text=prior_run_id).wait_for()
    wait_status(page, "succeeded")
    assert peer.take_request()["messages"][-1]["content"] == "Show a normal synthetic conversation"
    screenshot = screenshot_dir / "connected-synthetic.png"
    page.screenshot(path=str(screenshot), full_page=True)
    assert screenshot.is_file() and screenshot.stat().st_size > 0
    assert "A calm synthetic answer." in page.locator("#transcript").text_content()
    assert page.locator("#run-history button").first.text_content().startswith("succeeded")
    assert "verification:" not in page.locator("#run-status").text_content()
    assert "No verification requested" in page.locator("#run-status").text_content()
    page.set_viewport_size({"width": 390, "height": 844})
    overflow = page.evaluate("""() => [...document.querySelectorAll('*')]
      .filter(element => element.scrollWidth > element.clientWidth)
      .map(element => ({tag: element.tagName, id: element.id, className: element.className,
                        scrollWidth: element.scrollWidth, clientWidth: element.clientWidth}))""")
    assert not overflow, overflow
    page.locator("#message").wait_for(state="visible")
    assert page.locator("#token").input_value() == ""

    page.locator("#logout").click()
    page.locator("#workspace").wait_for(state="hidden")
    assert page.locator("#connection-status").text_content() == "Disconnected"
    assert page.locator("#token").input_value() == ""


def test_ambiguous_submission_retries_the_frozen_request_without_duplicate_work(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    gate = threading.Event()
    peer.enqueue(Reply(chunks=("accepted once",), gate=gate))
    connect(page, app)
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    accepted = {"run": None}

    def accept_then_drop(route):
        response = route.fetch()
        accepted["run"] = response.json()
        route.abort("connectionreset")

    page.route("**/v1/runs", accept_then_drop)
    send(page, "one durable request")
    peer.take_request()
    page.locator("#retry-send").wait_for(state="visible")
    page.unroute("**/v1/runs", accept_then_drop)
    page.locator("#retry-send").click()
    page.locator("#run-id").filter(has_text=accepted["run"]["id"]).wait_for()
    gate.set()
    wait_status(page, "succeeded")
    assert peer.requests.empty()
    listed = app.client.get(
        f'/v1/sessions/{accepted["run"]["request"]["session_id"]}/runs'
    ).json()
    assert [run["id"] for run in listed] == [accepted["run"]["id"]]


def test_cancel_reset_and_old_generation_history_remain_inspectable(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    gate = threading.Event()
    peer.enqueue(Reply(chunks=("partial", "never shown"), gate=gate, disconnected=threading.Event()), Reply(chunks=("new generation",)))
    connect(page, app)
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, "cancel this")
    peer.take_request()
    wait_status(page, "running")
    page.locator("#cancel-run").click()
    wait_status(page, "cancelled")
    assert "partial" in page.locator("#transcript").text_content()
    old_run_id = page.locator("#run-id").text_content()

    page.locator("#reset-session").click()
    page.locator("#generation").filter(has_text="1").wait_for()
    assert page.locator("#run-id").text_content() == old_run_id
    assert "cancelled" in page.locator("#run-status").text_content()
    send(page, "after reset")
    wait_status(page, "succeeded")
    assert peer.take_request()["messages"] == [{"role": "user", "content": "after reset"}]
    assert page.locator("#run-history button").count() == 2
    page.locator(f'#run-history button[data-run-id="{old_run_id}"]').click()
    wait_status(page, "cancelled")
    assert page.locator("#generation").text_content() == "1"


def test_approval_renders_exact_hostile_arguments_and_requires_fresh_review_after_stale_decision(
    web_service, browser_page
):
    app, peer = web_service
    page = browser_page
    hostile_path = "<img src=x onerror=window.pwned=true>.txt"
    peer.enqueue(
        Reply(frames=frames(
            message_start(),
            *tool_block(0, "write-hostile", "workspace_write", (
                json.dumps({"path": hostile_path, "content": "safe"}),
            )),
            *message_end(),
        )),
        Reply(chunks=("Verified synthetic write.",)),
    )
    connect(page, app)
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, "request approval")
    peer.take_request()
    approval = wait_approval(page)
    assert hostile_path in approval.text_content()
    assert approval.locator("img").count() == 0
    assert page.evaluate("window.pwned") is None

    decision_path = "/decision"
    intercepted = {"done": False}

    def forge_once(route, request):
        if not intercepted["done"] and request.url.endswith(decision_path):
            intercepted["done"] = True
            payload = json.loads(request.post_data)
            payload["arguments_sha256"] = "0" * 64
            route.continue_(post_data=json.dumps(payload))
        else:
            route.continue_()

    page.route("**/v1/approvals/*/decision", forge_once)
    approval.locator('button[data-decision="approve"]').click()
    page.locator("#errors").filter(has_text="approval_changed").wait_for()
    assert approval.locator("button:enabled").count() == 0
    assert not (app.root / "workspace" / hostile_path).exists()

    page.unroute("**/v1/approvals/*/decision", forge_once)
    page.locator("#refresh-approvals").click()
    page.locator('#approvals article button[data-decision="approve"]:enabled').wait_for()
    approval = wait_approval(page)
    assert hostile_path in approval.text_content()
    approval.locator('button[data-decision="approve"]').click()
    wait_status(page, "succeeded")
    assert (app.root / "workspace" / hostile_path).read_text() == "safe"
    assert "Verified synthetic write." in page.locator("#transcript").text_content()
    assert "Tool request: workspace_write" in page.locator("#activity").text_content()
    assert "succeeded" in page.locator("#receipts").text_content()


def test_newer_selection_and_logout_own_late_session_completions(web_service, browser_page):
    app, _peer = web_service
    page = browser_page
    session_a = app.client.post("/v1/sessions").json()
    session_b = app.client.post("/v1/sessions").json()
    connect(page, app)

    gate_fetch_completion(page, "select-a", "GET", f'/v1/sessions/{session_a["id"]}')
    page.locator(f'#sessions button[data-session-id="{session_a["id"]}"]').click()
    wait_fetch_gate(page, "select-a")
    page.locator(f'#sessions button[data-session-id="{session_b["id"]}"]').click()
    page.locator("#session-id").filter(has_text=session_b["id"]).wait_for()
    release_fetch_gate(page, "select-a")
    assert page.locator("#session-id").text_content() == session_b["id"]

    gate_fetch_completion(page, "logout-a", "GET", f'/v1/sessions/{session_a["id"]}')
    page.locator(f'#sessions button[data-session-id="{session_a["id"]}"]').click()
    wait_fetch_gate(page, "logout-a")
    page.locator("#logout").click()
    release_fetch_gate(page, "logout-a")
    assert page.evaluate("document.body.dataset.phase") == "disconnected"
    assert page.locator("#workspace").is_hidden()
    assert page.locator("#send").is_disabled()


def test_late_submission_stays_with_its_original_session(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    session_a = app.client.post("/v1/sessions").json()
    session_b = app.client.post("/v1/sessions").json()
    provider_gate = threading.Event()
    peer.enqueue(Reply(chunks=("late A result",), gate=provider_gate))
    connect(page, app)
    page.locator(f'#sessions button[data-session-id="{session_a["id"]}"]').click()
    page.locator("#session-id").filter(has_text=session_a["id"]).wait_for()

    gate_fetch_completion(page, "submit-a", "POST", "/v1/runs")
    send(page, "belongs to A")
    peer.take_request()
    wait_fetch_gate(page, "submit-a")
    accepted = app.client.get(f'/v1/sessions/{session_a["id"]}/runs').json()[0]
    page.locator(f'#sessions button[data-session-id="{session_b["id"]}"]').click()
    page.locator("#session-id").filter(has_text=session_b["id"]).wait_for()
    release_fetch_gate(page, "submit-a")
    assert page.locator("#session-id").text_content() == session_b["id"]
    assert page.locator(f'#run-history button[data-run-id="{accepted["id"]}"]').count() == 0

    page.locator(f'#sessions button[data-session-id="{session_a["id"]}"]').click()
    page.locator(f'#run-history button[data-run-id="{accepted["id"]}"]').wait_for()
    provider_gate.set()
    wait_status(page, "succeeded")


def test_aborted_observer_retry_cannot_replace_manual_reconnect(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    provider_gate = threading.Event()
    peer.enqueue(Reply(chunks=("partial", " complete"), gate=provider_gate))
    connect(page, app)
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, "hold the run")
    peer.take_request()
    wait_status(page, "running")

    page.evaluate("""() => {
      window.__eventFetches = 0;
      window.__retryCallbacks = [];
      window.__realSetTimeout = window.setTimeout.bind(window);
      const priorFetch = window.fetch;
      window.__staleOriginalFetch = priorFetch;
      window.fetch = (...args) => {
        const input = args[0];
        const url = new URL(input instanceof Request ? input.url : input, location.href);
        if (url.pathname.endsWith('/events')) window.__eventFetches += 1;
        return priorFetch(...args);
      };
      window.setTimeout = (callback, delay, ...args) => {
        if (delay === 250) {
          window.__retryCallbacks.push(() => callback(...args));
          return 8675309;
        }
        return window.__realSetTimeout(callback, delay, ...args);
      };
    }""")
    routed = {"count": 0}

    def fail_first_reconnect(route):
        routed["count"] += 1
        if routed["count"] == 1:
            route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": {"code": "synthetic_stream_failure", "message": "retry me"}}),
            )
        else:
            route.continue_()

    page.route("**/v1/runs/*/events?*", fail_first_reconnect)
    page.locator("#reconnect-stream").click()
    page.wait_for_function("() => window.__retryCallbacks.length === 1")
    page.locator("#reconnect-stream").click()
    page.wait_for_function("() => window.__eventFetches === 2")
    fetches = page.evaluate("""async () => {
      window.__retryCallbacks.shift()();
      await window.__staleOriginalFetch('/healthz', {cache: 'no-store'});
      await new Promise(resolve => window.__realSetTimeout(resolve, 0));
      return window.__eventFetches;
    }""")
    assert fetches == 2
    provider_gate.set()
    wait_status(page, "succeeded")


def test_hash_and_run_history_recover_records_beyond_first_page(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    sessions = [app.client.post("/v1/sessions").json() for _ in range(51)]
    oldest_session = sessions[0]
    created_runs = []
    for number in range(51):
        peer.enqueue(Reply(chunks=(f"history result {number}",)))
        run = submit(app, f"history request {number}", oldest_session, request_id=f"history-{number}")
        assert events(app, run["id"])[-1]["data"]["status"] == "succeeded"
        created_runs.append(run)
    oldest_run = created_runs[0]

    page.goto(app.url + f'#session={oldest_session["id"]}&run={oldest_run["id"]}')
    page.locator("#token").fill((app.root / "token").read_text().strip())
    page.locator("#connect").click()
    page.locator("#session-id").filter(has_text=oldest_session["id"]).wait_for()
    page.locator("#run-id").filter(has_text=oldest_run["id"]).wait_for()
    assert page.locator(f'#sessions button[data-session-id="{oldest_session["id"]}"]').count() == 1
    assert page.locator(f'#run-history button[data-run-id="{oldest_run["id"]}"]').count() == 1
    page.locator("#load-older-runs").wait_for(state="visible")
    page.locator("#load-older-runs").click()
    page.locator("#load-older-runs").wait_for(state="hidden")
    assert page.locator("#run-history button").count() == 51

    page.locator("#logout").click()
    page.evaluate(
        "hash => { location.hash = hash; }",
        f'session={sessions[1]["id"]}&run={oldest_run["id"]}',
    )
    page.locator("#token").fill((app.root / "token").read_text().strip())
    page.locator("#connect").click()
    page.locator("#errors").filter(has_text="run_session_mismatch").wait_for()
    assert page.locator("#session-id").text_content() == sessions[1]["id"]
    assert page.locator("#run-id").text_content() == "None"
