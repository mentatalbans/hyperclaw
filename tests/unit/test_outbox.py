"""Outbox queue: caps, TTL, session binding."""
import time

import pytest

from hyperclaw import outbox


@pytest.fixture(autouse=True)
def _clean():
    outbox._queues.clear()
    outbox.set_current_session(None)
    yield
    outbox._queues.clear()
    outbox.set_current_session(None)


def test_no_session_returns_guidance(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    msg = outbox.queue_file(str(f))
    assert "No active conversation" in msg


def test_queue_and_drain(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    outbox.set_current_session(42)
    assert "Queued" in outbox.queue_file(str(f), caption="hi")
    files = outbox.drain(42)
    assert len(files) == 1
    assert files[0]["caption"] == "hi"
    assert outbox.drain(42) == []  # drained


def test_missing_file(tmp_path):
    outbox.set_current_session(42)
    assert "not found" in outbox.queue_file(str(tmp_path / "nope.txt")).lower()


def test_queue_cap(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    outbox.set_current_session(7)
    for _ in range(outbox.MAX_QUEUE_PER_CHAT + 25):
        outbox.queue_file(str(f))
    assert len(outbox._queues[7]) == outbox.MAX_QUEUE_PER_CHAT


def test_stale_entries_dropped_at_drain(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("x")
    outbox.set_current_session(9)
    outbox.queue_file(str(f))
    # Age the entry past the TTL
    outbox._queues[9][0]["queued_at"] = time.time() - outbox.ENTRY_TTL_SECONDS - 1
    outbox.queue_file(str(f))  # fresh one
    files = outbox.drain(9)
    assert len(files) == 1


def test_explicit_chat_id_beats_thread_binding(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    outbox.set_current_session(1)
    outbox.queue_file(str(f), chat_id=2)
    assert outbox.drain(1) == []
    assert len(outbox.drain(2)) == 1


def test_kind_of():
    assert outbox.kind_of("x.png") == "photo"
    assert outbox.kind_of("x.mp4") == "video"
    assert outbox.kind_of("x.mp3") == "audio"
    assert outbox.kind_of("x.pdf") == "document"
    assert outbox.kind_of("noext") == "document"
