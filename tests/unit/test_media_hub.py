"""media_hub: senders return error strings (never raise); AppleScript stays single-line safe."""
from unittest import mock

from hyperclaw import media_hub


def test_missing_file_returns_error_not_raise():
    assert "not found" in media_hub.telegram_send_file("/nope/x.png").lower()
    assert "not found" in media_hub.imessage_send_file("/nope/x.png", recipient="+15551234567").lower()
    assert "not found" in media_hub.email_send_file("/nope/x.png", to="a@b.c").lower()
    assert "failed" in media_hub.open_file("/nope/x.png").lower()


def test_deliver_file_unknown_channel(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert "Unknown delivery channel" in media_hub.deliver_file(str(f), via="carrier-pigeon")


def test_multiline_caption_produces_single_line_applescript(tmp_path, monkeypatch):
    f = tmp_path / "report.pdf"
    f.write_text("x")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["script"] = cmd[-1]
        return mock.Mock(returncode=0, stderr="")

    monkeypatch.setattr(media_hub.subprocess, "run", fake_run)
    out = media_hub.imessage_send_file(str(f), recipient="+15551234567",
                                       message="line one\nline two\r\nline three")
    assert "Sent" in out
    # The message text inside the AppleScript string literal must not contain raw newlines
    script = captured["script"]
    send_line = next(l for l in script.splitlines() if l.strip().startswith('send "'))
    assert "line one" in send_line and "line three" in send_line


def test_applescript_uses_service_buddy_dialect(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("x")
    captured = {}
    monkeypatch.setattr(media_hub.subprocess, "run",
                        lambda cmd, **k: (captured.__setitem__("s", cmd[-1]),
                                          mock.Mock(returncode=0, stderr=""))[1])
    media_hub.imessage_send_file(str(f), recipient="+15551234567")
    assert "1st service whose service type = iMessage" in captured["s"]
    assert 'buddy "+15551234567"' in captured["s"]


def test_open_file_timeout_returns_error(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("x")

    def raise_timeout(cmd, **kwargs):
        raise media_hub.subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(media_hub.subprocess, "run", raise_timeout)
    assert "timed out" in media_hub.open_file(str(f))


def test_telegram_unconfigured(tmp_path, monkeypatch):
    f = tmp_path / "a.png"
    f.write_text("x")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert "not configured" in media_hub.telegram_send_file(str(f))
