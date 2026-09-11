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


def focus_indicator(locator):
    return locator.evaluate("""element => {
      const style = getComputedStyle(element);
      const outlineVisible = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0 &&
        style.outlineColor !== 'transparent' && style.outlineColor !== 'rgba(0, 0, 0, 0)';
      return {visible: outlineVisible || style.boxShadow !== 'none',
              outline: `${style.outlineWidth} ${style.outlineStyle} ${style.outlineColor}`,
              boxShadow: style.boxShadow};
    }""")


def keyboard_reach(page, selector, *, limit=80):
    target = page.locator(selector).first
    for _ in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("element => document.activeElement === element"):
            assert target.is_visible()
            assert target.evaluate("element => element.matches(':focus-visible')")
            indicator = focus_indicator(target)
            assert indicator["visible"], f"No rendered focus indicator for {selector}: {indicator}"
            return target
    focused = page.evaluate("document.activeElement && (document.activeElement.id || document.activeElement.outerHTML)")
    raise AssertionError(f"Keyboard focus did not reach {selector}; stopped at {focused}")


def browser_storage_inventory(page):
    return page.evaluate("""async () => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
      cookies: document.cookie,
      databases: indexedDB.databases ? (await indexedDB.databases()).map(database => database.name) : [],
      caches: 'caches' in window ? await caches.keys() : [],
    })""")


def test_focus_indicator_check_rejects_invisible_keyboard_focus(browser_page):
    page = browser_page
    page.set_content("""<style>
      button:focus-visible { outline: 3px solid rgb(1, 2, 3); }
    </style><button>Focusable sentinel</button>""")
    page.keyboard.press("Tab")
    button = page.get_by_role("button", name="Focusable sentinel")
    assert focus_indicator(button)["visible"]

    page.add_style_tag(content="button:focus-visible { outline: none !important; box-shadow: none !important; }")
    assert not focus_indicator(button)["visible"]


def test_browser_storage_inventory_detects_indexeddb_and_cache_sentinels(web_service, browser_page):
    app, _peer = web_service
    page = browser_page
    page.goto(app.url)
    assert browser_storage_inventory(page) == {
        "local": [], "session": [], "cookies": "", "databases": [], "caches": [],
    }

    page.evaluate("""async () => {
      await new Promise((resolve, reject) => {
        const request = indexedDB.open('opaque-indexeddb-sentinel', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('values').put(
          'opaque-record-value', 'opaque-record-key');
        request.onsuccess = () => { request.result.close(); resolve(); };
        request.onerror = () => reject(request.error);
      });
      const cache = await caches.open('opaque-cache-sentinel');
      await cache.put('/opaque-cache-key', new Response('opaque-cache-value'));
    }""")

    inventory = browser_storage_inventory(page)
    assert inventory["databases"] == ["opaque-indexeddb-sentinel"]
    assert inventory["caches"] == ["opaque-cache-sentinel"]


def test_operator_announcements_are_exposed_in_the_accessibility_tree(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    page.goto(app.url)

    page.locator("#connect").click()
    assert page.locator("#errors").aria_snapshot() == '- alert: Enter the operator token.'

    peer.enqueue(Reply())
    page.locator("#token").fill((app.root / "token").read_text().strip())
    page.locator("#connect").click()
    page.locator("#workspace").wait_for(state="visible")
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, "announce the terminal state")
    wait_status(page, "succeeded")

    assert page.locator("#connection-status").aria_snapshot() == '- status: Connected'
    assert page.locator("#run-status").aria_snapshot() == (
        '- status: "Run: succeeded · No verification requested"'
    )


def test_keyboard_only_operator_workflow_has_visible_focus_and_exact_decisions(
    web_service, browser_page, request
):
    app, peer = web_service
    page = browser_page
    skill_dir = app.root / "skills" / "keyboard-guidance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: keyboard-guidance\ndescription: Synthetic keyboard guidance.\n---\n"
        "Keep operator controls reachable.\n"
    )
    skill = Skills(app.root / "skills").load("keyboard-guidance")
    app.client.post(
        "/v1/skills/keyboard-guidance/admit", json={"content_hash": skill.content_hash}
    ).raise_for_status()
    peer.enqueue(
        Reply(frames=frames(
            message_start(),
            *tool_block(0, "keyboard-approved", "workspace_write", (
                '{"path":"keyboard-approved.txt","content":"approved"}',
            )),
            *message_end(),
        )),
        Reply(chunks=("Approval completed.",)),
        Reply(frames=frames(
            message_start(),
            *tool_block(0, "keyboard-denied", "workspace_write", (
                '{"path":"keyboard-denied.txt","content":"denied"}',
            )),
            *message_end(),
        )),
    )
    decisions = []
    page.on("request", lambda outgoing: decisions.append(json.loads(outgoing.post_data))
            if "/v1/approvals/" in outgoing.url and outgoing.url.endswith("/decision") else None)

    page.goto(app.url)
    keyboard_reach(page, "#token").type((app.root / "token").read_text().strip())
    keyboard_reach(page, "#connect").press("Enter")
    page.locator("#workspace").wait_for(state="visible")
    keyboard_reach(page, "#new-session").press("Enter")
    page.locator("#session-id").filter(has_not_text="None").wait_for()

    keyboard_reach(page, "details summary").press("Enter")
    tool = keyboard_reach(page, 'input[name="tools"][value="workspace_read"]')
    tool.press("Space")
    assert not tool.is_checked()
    selected_skill = keyboard_reach(page, 'input[name="skills"][value="keyboard-guidance"]')
    selected_skill.press("Space")
    assert selected_skill.is_checked()
    assert app.client.get(f'/v1/sessions/{page.locator("#session-id").text_content()}/runs').json() == []

    message = keyboard_reach(page, "#message")
    message.type("keyboard approval request")
    message.press("Enter")
    assert peer.requests.empty(), "Enter in the multiline composer must not submit"
    assert "\n" in message.input_value()
    keyboard_reach(page, "#send").press("Enter")
    submitted = peer.take_request()
    assert submitted["messages"][-1]["content"] == "keyboard approval request\n"
    assert "workspace_read" not in [tool["name"] for tool in submitted["tools"]]
    assert "Keep operator controls reachable." in submitted["system"]

    approval = wait_approval(page)
    exact_approval = json.loads(approval.locator("pre").text_content())
    keyboard_reach(page, 'button[data-decision="approve"]').press("Enter")
    wait_status(page, "succeeded")
    assert decisions[-1] == {
        "approved": True,
        "arguments_sha256": exact_approval["arguments_sha256"],
        "policy_sha256": exact_approval["policy_sha256"],
    }
    assert (app.root / "workspace" / "keyboard-approved.txt").read_text() == "approved"
    continued = peer.take_request()
    assert continued["messages"][-1]["content"][0]["tool_use_id"] == "keyboard-approved"
    approved_run_id = page.locator("#run-id").text_content()

    message = keyboard_reach(page, "#message")
    message.type("keyboard denial request")
    keyboard_reach(page, "#send").press("Enter")
    assert peer.take_request()["messages"][-1]["content"] == "keyboard denial request"
    approval = wait_approval(page)
    exact_denial = json.loads(approval.locator("pre").text_content())
    keyboard_reach(page, 'button[data-decision="deny"]').press("Enter")
    wait_status(page, "failed")
    assert decisions[-1] == {
        "approved": False,
        "arguments_sha256": exact_denial["arguments_sha256"],
        "policy_sha256": exact_denial["policy_sha256"],
    }
    assert not (app.root / "workspace" / "keyboard-denied.txt").exists()

    gate = threading.Event()
    peer.enqueue(Reply(chunks=("keyboard cancellation prefix", " hidden suffix"), gate=gate))
    message = keyboard_reach(page, "#message")
    message.type("keyboard cancellation request")
    keyboard_reach(page, "#send").press("Enter")
    peer.take_request()
    page.locator("#transcript").filter(has_text="keyboard cancellation prefix").wait_for()
    keyboard_reach(page, "#cancel-run").press("Enter")
    wait_status(page, "cancelled")
    assert "keyboard cancellation prefix" in page.locator("#transcript").text_content()

    keyboard_reach(page, f'#run-history button[data-run-id="{approved_run_id}"]').press("Enter")
    wait_status(page, "succeeded")
    assert page.locator("#run-id").text_content() == approved_run_id

    accessibility = page.locator("main").aria_snapshot()
    for accessible_control in (
        "button \"Disconnect\"", "button \"New session\"", "button \"Send\"",
        "button \"Cancel run\"", "button \"Refresh approvals\"",
        "textbox \"Message\"", "checkbox \"workspace_write\"",
        "checkbox \"keyboard-guidance — Synthetic keyboard guidance.\"",
    ):
        assert accessible_control in accessibility
    screenshot_dir = Path(request.config.getoption("--browser-screenshot-dir"))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    (screenshot_dir / "keyboard-accessibility.yml").write_text(accessibility)

    keyboard_reach(page, "#logout").press("Enter")
    page.locator("#workspace").wait_for(state="hidden")
    assert page.locator("#connection-status").aria_snapshot() == '- status: Disconnected'


@pytest.mark.parametrize("width,zoom", [(320, 1), (390, 1), (1440, 1), (1440, 2)])
def test_responsive_operator_surface_wraps_synthetic_hostile_content_without_leaks(
    web_service, browser_page, request, width, zoom
):
    app, peer = web_service
    page = browser_page
    console_errors = []
    page_errors = []
    failed_requests = []
    requested_urls = []
    page.on("console", lambda message: console_errors.append(message.text)
            if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", lambda outgoing: failed_requests.append(outgoing.url))
    page.on("request", lambda outgoing: requested_urls.append(outgoing.url))
    long_path = "synthetic-<img-src=x-onerror=window.pwned=true>-" + "x" * 140 + ".txt"
    long_text = "Synthetic hostile <script>window.pwned=true</script> " + "Y" * 220
    peer.enqueue(
        Reply(frames=frames(
            message_start(),
            *tool_block(0, "synthetic-long-tool-call-id-" + "c" * 96, "workspace_write", (
                json.dumps({"path": long_path, "content": long_text}),
            )),
            *message_end(),
        )),
        Reply(chunks=(long_text,)),
    )

    connect(page, app)
    token = (app.root / "token").read_text().strip()
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, long_text)
    peer.take_request()
    approval = wait_approval(page)
    approval.locator('button[data-decision="approve"]').click()
    wait_status(page, "succeeded")
    page.locator("#receipts").filter(has_text=long_path).wait_for()

    if zoom == 1:
        page.set_viewport_size({"width": width, "height": 900})
    else:
        page.set_viewport_size({"width": width // zoom, "height": 900 // zoom})
    page.locator("#logout").scroll_into_view_if_needed()
    essential_controls = (
        "#logout", "#new-session", "#reset-session", "#cancel-run", "#reconnect-stream",
        "#message", "#send", "details summary", "#run-history button", "#refresh-approvals",
    )
    for selector in essential_controls:
        control = page.locator(selector).first
        assert control.is_visible(), selector
        assert control.evaluate("""element => {
          const bounds = element.getBoundingClientRect();
          return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }"""), f"essential control is outside the horizontal viewport: {selector}"
    assert page.evaluate("window.pwned") is None
    assert page.evaluate("""() => document.documentElement.scrollWidth <= window.innerWidth &&
      document.body.scrollWidth <= window.innerWidth""")
    overflow = page.evaluate("""() => [...document.querySelectorAll('body *')]
      .filter(element => element.scrollWidth > element.clientWidth &&
        getComputedStyle(element).overflowX === 'visible')
      .map(element => ({tag: element.tagName, id: element.id,
                        scrollWidth: element.scrollWidth, clientWidth: element.clientWidth}))""")
    assert not overflow, overflow
    assert page.evaluate("secret => !document.documentElement.innerHTML.includes(secret)", token), (
        "operator token appeared in rendered markup"
    )
    assert page.evaluate("secret => !location.href.includes(secret)", token), (
        "operator token appeared in the current URL"
    )
    assert browser_storage_inventory(page) == {
        "local": [], "session": [], "cookies": "", "databases": [], "caches": [],
    }
    assert all(page.evaluate("([url, secret]) => !url.includes(secret)", [url, token])
               for url in requested_urls), "operator token appeared in a requested URL"

    screenshot_dir = Path(request.config.getoption("--browser-screenshot-dir"))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{width}px" if zoom == 1 else f"{width}px-effective-viewport-200pct"
    screenshot = screenshot_dir / f"responsive-synthetic-{suffix}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    assert screenshot.is_file() and screenshot.stat().st_size > 0
    assert console_errors == []
    assert page_errors == []
    assert failed_requests == []


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


def test_normal_conversation_has_no_browser_or_favicon_errors(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    console_errors = []
    page_errors = []
    failed_requests = []
    favicon_requests = []
    favicon_responses = []
    page.on("console", lambda message: console_errors.append({
        "text": message.text, "location": message.location,
    }) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("request", lambda request: favicon_requests.append(request.url)
            if request.url.endswith("/favicon.ico") else None)
    page.on("requestfailed", lambda request: failed_requests.append({
        "url": request.url, "failure": request.failure,
    }))
    page.on("response", lambda response: favicon_responses.append({
        "url": response.url, "status": response.status,
    }) if response.url.endswith("/favicon.ico") else None)
    peer.enqueue(Reply(chunks=("A normal synthetic reply.",)))

    connect(page, app)
    page.locator("#new-session").click()
    page.locator("#session-id").filter(has_not_text="None").wait_for()
    send(page, "normal console check")
    assert peer.take_request()["messages"][-1]["content"] == "normal console check"
    wait_status(page, "succeeded")
    assert "A normal synthetic reply." in page.locator("#transcript").text_content()
    page.locator("#logout").click()
    page.locator("#workspace").wait_for(state="hidden")

    assert favicon_requests == []
    assert favicon_responses == []
    assert failed_requests == []
    assert page_errors == []
    assert console_errors == []


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
    page.locator("#transcript").filter(has_text="partial").wait_for()
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


def test_explicit_retry_rebinds_to_returned_original_session(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    session_a = app.client.post("/v1/sessions").json()
    session_b = app.client.post("/v1/sessions").json()
    provider_gate = threading.Event()
    peer.enqueue(Reply(chunks=("accepted retry",), gate=provider_gate))
    connect(page, app)
    page.locator(f'#sessions button[data-session-id="{session_a["id"]}"]').click()
    page.locator("#session-id").filter(has_text=session_a["id"]).wait_for()

    page.evaluate("""() => {
      const acceptedFetch = window.fetch;
      window.__beforeAcceptance = {seen: false, bodies: [], release: null};
      window.fetch = async (input, options = {}) => {
        const request = input instanceof Request ? input : null;
        const url = new URL(request ? request.url : input, location.href);
        const method = (options.method || (request && request.method) || 'GET').toUpperCase();
        if (method === 'POST' && url.pathname === '/v1/runs') {
          window.__beforeAcceptance.bodies.push(options.body);
          if (window.__beforeAcceptance.bodies.length === 1) {
            window.__beforeAcceptance.seen = true;
            await new Promise(resolve => { window.__beforeAcceptance.release = resolve; });
            throw new TypeError('Synthetic failure before acceptance');
          }
        }
        return acceptedFetch(input, options);
      };
    }""")
    send(page, "retry after returning")
    page.wait_for_function("() => window.__beforeAcceptance.seen")
    assert app.client.get(f'/v1/sessions/{session_a["id"]}/runs').json() == []
    page.evaluate("window.__beforeAcceptance.release()")
    page.locator("#retry-send").wait_for(state="visible")

    page.locator(f'#sessions button[data-session-id="{session_b["id"]}"]').click()
    page.locator("#session-id").filter(has_text=session_b["id"]).wait_for()
    assert page.locator("#retry-send").is_hidden()
    page.locator(f'#sessions button[data-session-id="{session_a["id"]}"]').click()
    page.locator("#session-id").filter(has_text=session_a["id"]).wait_for()
    page.locator("#retry-send").wait_for(state="visible")
    page.locator("#retry-send").click()

    assert peer.take_request()["messages"][-1]["content"] == "retry after returning"
    accepted = app.client.get(f'/v1/sessions/{session_a["id"]}/runs').json()[0]
    page.locator("#run-id").filter(has_text=accepted["id"]).wait_for()
    assert page.evaluate("window.__beforeAcceptance.bodies[0] === window.__beforeAcceptance.bodies[1]")
    provider_gate.set()
    wait_status(page, "succeeded")
    assert peer.requests.empty(), "only the accepted retry may reach the provider"


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


@pytest.fixture
def configured_mcp_service(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url)
    docs = tmp_path / 'public-docs'
    docs.mkdir()
    (docs / 'guide.md').write_text('Synthetic public documentation.\n')
    config = app.root / 'config.toml'
    config.write_text(config.read_text().replace('mcp_docs_path = ""', f'mcp_docs_path = {json.dumps(str(docs))}')
                      .replace('mcp_docs_image = ""', 'mcp_docs_image = "sha256:' + '0' * 64 + '"'))
    try:
        app.start()
        yield app, peer, docs
    finally:
        app.stop()
        peer.close()


def admit_browser_docs(app):
    preview = app.client.get('/v1/mcp')
    preview.raise_for_status()
    app.client.post('/v1/mcp/admit', json={'expected_sha256': preview.json()['sha256']}).raise_for_status()


def test_optional_mcp_discovery_failure_preserves_core_web_work(configured_mcp_service, browser_page):
    app, peer, docs = configured_mcp_service
    page = browser_page
    (docs / 'guide.md').unlink()
    docs.rmdir()
    assert app.client.get('/v1/mcp').status_code == 422
    peer.enqueue(Reply(chunks=('Ordinary chat is available.',)))
    page.goto(app.url)
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    page.locator('#workspace').wait_for(state='visible', timeout=3000)
    warning = page.locator('#discovery-errors')
    warning.filter(has_text='invalid_mcp_docs').wait_for(state='visible')
    assert 'MCP' in warning.text_content() and 'reconnect' in warning.text_content().lower()
    assert page.locator('input[name="tools"][value^="mcp_"]:disabled').count() == 2
    assert page.locator('input[name="tools"][value^="mcp_"]:checked').count() == 0
    page.locator('#new-session').click()
    page.locator('#session-id').filter(has_not_text='None').wait_for()
    assert page.locator('#reset-session').is_enabled()
    page.locator('#refresh-approvals').click()
    page.locator('#approvals').filter(has_text='No waiting approvals.').wait_for()
    send(page, 'Ordinary chat with missing optional documentation')
    wait_status(page, 'succeeded')
    assert peer.take_request()['messages'][-1]['content'] == 'Ordinary chat with missing optional documentation'
    assert 'Ordinary chat is available.' in page.locator('#transcript').text_content()
    assert page.locator('#run-history button').count() == 1
    assert warning.is_visible() and 'invalid_mcp_docs' in warning.text_content()
    gate = threading.Event()
    peer.enqueue(Reply(gate=gate, disconnected=threading.Event()))
    send(page, 'Cancel this ordinary run')
    peer.take_request()
    wait_status(page, 'running')
    page.locator('#cancel-run').click()
    wait_status(page, 'cancelled')
    page.locator('#reset-session').click()
    page.locator('#generation').filter(has_text='1').wait_for()
    page.locator('#logout').click()
    assert warning.text_content() == ''
    # Restoring and admitting real local documentation enables selection on a fresh connection.
    docs.mkdir()
    (docs / 'guide.md').write_text('Synthetic public documentation.\n')
    admit_browser_docs(app)
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    page.locator('#workspace').wait_for(state='visible')
    assert warning.text_content() == ''
    assert page.locator('input[name="tools"][value^="mcp_"]:enabled').count() == 2
    assert page.locator('input[name="tools"][value^="mcp_"]:checked').count() == 0
    page.locator('details summary').click()
    page.locator('input[value="mcp_docs_read"]').check()
    assert page.locator('input[value="mcp_docs_read"]').is_checked()


def test_login_errors_are_visible_and_clear_on_success_and_logout(web_service, browser_page):
    app, _peer = web_service
    page = browser_page
    page.goto(app.url)
    page.locator('#connect').click()
    page.locator('#errors').filter(has_text='Enter the operator token.').wait_for(state='visible', timeout=3000)
    page.locator('#token').fill('invalid-synthetic-token')
    page.locator('#connect').click()
    page.locator('#errors').filter(has_text='unauthorized').wait_for(state='visible', timeout=3000)
    assert page.locator('#workspace').is_hidden()
    assert page.locator('#token').input_value() == ''
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    page.locator('#workspace').wait_for(state='visible')
    assert page.locator('#errors').text_content() == ''
    page.route('**/v1/approvals', lambda route: route.fulfill(status=503, content_type='application/json',
        body=json.dumps({'error': {'code': 'synthetic_unavailable', 'message': 'Try refresh again.'}})))
    page.locator('#refresh-approvals').click()
    page.locator('#errors').filter(has_text='synthetic_unavailable').wait_for(state='visible')
    page.locator('#logout').click()
    assert page.locator('#errors').text_content() == ''
    assert page.locator('#workspace').is_hidden()


def test_optional_skill_discovery_failure_keeps_chat_without_skill_selection(web_service, browser_page):
    app, peer = web_service
    page = browser_page
    page.route('**/v1/skills', lambda route: route.fulfill(status=503, content_type='application/json',
        body=json.dumps({'error': {'code': 'synthetic_skills_unavailable', 'message': 'Review the skills directory.'}})))
    peer.enqueue(Reply())
    page.goto(app.url)
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    page.locator('#workspace').wait_for(state='visible', timeout=3000)
    page.locator('#discovery-errors').filter(has_text='synthetic_skills_unavailable').wait_for(state='visible')
    assert page.locator('input[name="skills"]').count() == 0
    page.locator('#new-session').click()
    page.locator('#session-id').filter(has_not_text='None').wait_for()
    send(page, 'Chat without optional skills')
    wait_status(page, 'succeeded')
    run_id = page.locator('#run-id').text_content()
    assert app.client.get(f'/v1/runs/{run_id}').json()['request']['skills'] == []


@pytest.mark.parametrize('path,status', [('/v1/skills', 401), ('/v1/mcp', 403)])
def test_optional_discovery_auth_failure_still_disconnects(web_service, browser_page, path, status):
    app, _peer = web_service
    page = browser_page
    page.route('**' + path, lambda route: route.fulfill(status=status, content_type='application/json',
        body=json.dumps({'error': {'code': 'synthetic_auth_failure', 'message': 'Reconnect with an authorized token.'}})))
    page.goto(app.url)
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    page.locator('#errors').filter(has_text='synthetic_auth_failure').wait_for(state='visible', timeout=3000)
    assert page.locator('#workspace').is_hidden()
    assert page.evaluate('document.body.dataset.phase') == 'disconnected'
    assert page.locator('#token').input_value() == ''


@pytest.mark.parametrize('old_auth_failure', [False, True])
def test_new_connection_owns_late_optional_discovery(configured_mcp_service, browser_page, old_auth_failure):
    app, _peer, docs = configured_mcp_service
    page = browser_page
    admit_browser_docs(app)
    if old_auth_failure:
        page.route('**/v1/mcp', lambda route: route.fulfill(status=401, content_type='application/json',
            body=json.dumps({'error': {'code': 'old_auth_failure', 'message': 'Stale connection.'}})), times=1)
    page.goto(app.url)
    gate_fetch_completion(page, 'old-mcp', 'GET', '/v1/mcp')
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    wait_fetch_gate(page, 'old-mcp')
    if not old_auth_failure:
        (docs / 'guide.md').unlink()
        docs.rmdir()
    page.locator('#token').fill((app.root / 'token').read_text().strip())
    page.locator('#connect').click()
    page.locator('#workspace').wait_for(state='visible', timeout=3000)
    release_fetch_gate(page, 'old-mcp')
    assert page.locator('#workspace').is_visible()
    assert page.locator('#connection-status').text_content() == 'Connected'
    assert page.locator('#errors').text_content() == ''
    if old_auth_failure:
        assert page.locator('#discovery-errors').text_content() == ''
        assert page.locator('input[name="tools"][value^="mcp_"]:enabled').count() == 2
    else:
        assert 'invalid_mcp_docs' in page.locator('#discovery-errors').text_content()
        assert page.locator('input[name="tools"][value^="mcp_"]:disabled').count() == 2
    assert page.locator('input[name="tools"][value^="mcp_"]:checked').count() == 0
