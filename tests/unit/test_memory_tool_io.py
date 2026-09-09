"""File-backed tool commits yield to the loop and settle before cancellation exits."""

import asyncio
from pathlib import Path
import threading

import pytest

from hyperclaw.memory_manager import MemoryManager
from hyperclaw.memory_tools import bind_memory_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["store", "forget"])
@pytest.mark.parametrize("fail", [False, True], ids=["committed", "write-failed"])
async def test_slow_file_tool_honors_cancellation_without_blocking_or_stale_cache(
    tmp_path, monkeypatch, operation, fail,
):
    memory = MemoryManager(root=tmp_path)
    await memory.initialize()
    identifier = await memory.remember("observatory test fact") if operation == "forget" else None
    release = threading.Event()
    timer = threading.Timer(0.15, release.set)
    if operation == "store":
        write = memory._write_json_atomic
        def slow_write(path, value):
            release.wait(2)
            if fail:
                raise OSError("synthetic disk failure")
            write(path, value)
        monkeypatch.setattr(memory, "_write_json_atomic", slow_write)
    else:
        unlink = Path.unlink
        def slow_unlink(path, *args, **kwargs):
            release.wait(2)
            if fail:
                raise OSError("synthetic disk failure")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", slow_unlink)

    heartbeat = []
    async def tick():
        await asyncio.sleep(0.01)
        heartbeat.append(not release.is_set())

    execute = bind_memory_tools(memory, lambda name, inputs: pytest.fail(name))
    inputs = {"content": "observatory test fact"} if operation == "store" else {"memory_id": identifier}
    ticker = asyncio.create_task(tick())
    timer.start()
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(execute(f"memory_{operation}", inputs), timeout=0.03)
    finally:
        release.set()
        timer.join()
        await ticker
    assert heartbeat == [True], "disk operations must let other requests run"
    # An in-progress disk commit cannot be killed. Cancellation settles it and
    # publishes the matching cache before releasing the memory mutation lock.
    fresh = MemoryManager(root=tmp_path)
    await fresh.initialize()
    durable = await fresh.recall("observatory")
    assert await memory.recall("observatory") == durable
    assert len(durable) == int((operation == "store") != fail)


@pytest.mark.asyncio
async def test_concurrent_file_mutations_preserve_other_memories(tmp_path):
    memory = MemoryManager(root=tmp_path)
    await memory.initialize()
    original = await memory.remember("old observatory fact")
    first, deleted, second = await asyncio.gather(
        memory.remember("first observatory fact"),
        memory.forget(original),
        memory.remember("second observatory fact"),
    )
    assert deleted is True
    assert {item.id for item in await memory.recall("observatory")} == {first, second}
    fresh = MemoryManager(root=tmp_path)
    await fresh.initialize()
    assert sorted(await memory.recall("observatory"), key=lambda item: item.id) == sorted(
        await fresh.recall("observatory"), key=lambda item: item.id,
    )
