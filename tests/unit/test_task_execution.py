"""Regression tests for shared ownership of queued and explicit task execution."""

import asyncio
from collections import Counter

import pytest

from hyperclaw.agent_coordinator import AgentConfig, AgentCoordinator, TaskStatus

pytestmark = pytest.mark.asyncio


class LocalRouter:
    """Controlled model boundary; no SDK clients or network requests."""

    def __init__(self, plan: str = "SIMPLE: answer", failure: bool = False):
        self.plan = plan
        self.failure = failure
        self.executions: Counter[str] = Counter()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def call(self, message: str, **kwargs) -> tuple[str, dict]:
        if "task" not in kwargs.get("context", {}):
            if message.startswith("Analyze this goal"):
                return self.plan, {}
            return "synthesized", {}
        self.executions[message] += 1
        execution = self.executions[message]
        self.started.set()
        await self.release.wait()
        if self.failure:
            raise RuntimeError("model unavailable")
        return f"{message}: execution {execution}", {"model_name": "local-test"}


@pytest.fixture
def coordinator(monkeypatch, tmp_path):
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    instance = AgentCoordinator(LocalRouter())
    instance.agents = {
        "TEST": AgentConfig("TEST", "Test", "general", "Test specialist")
    }
    return instance


@pytest.mark.parametrize("with_worker", [False, True])
async def test_competing_calls_share_one_execution(coordinator, with_worker):
    router = coordinator.model_router
    router.release.clear()
    task = await coordinator.submit_task("one operation")
    if with_worker:
        await coordinator.start_workers(1)
    callers = [asyncio.create_task(coordinator.execute_task(task)) for _ in range(4)]
    try:
        await asyncio.wait_for(router.started.wait(), timeout=2)
        router.release.set()
        results = await asyncio.wait_for(asyncio.gather(*callers), timeout=2)
        if with_worker:
            await asyncio.wait_for(coordinator.task_queue.join(), timeout=2)
        assert results == ["one operation: execution 1"] * 4
        assert router.executions == {"one operation": 1}
        assert task.status is TaskStatus.COMPLETED
        assert task.completed_at is not None
        assert await coordinator.execute_task(task) == "one operation: execution 1"
    finally:
        router.release.set()
        await coordinator.stop_workers()


@pytest.mark.parametrize(
    "plan, expected",
    [("SIMPLE: answer", {"answer": 1}), ("SUBTASKS:\n1. first\n2. second", {"first": 1, "second": 1})],
)
async def test_coordinate_and_workers_do_not_repeat_submitted_tasks(coordinator, plan, expected):
    coordinator.model_router.plan = plan
    await coordinator.start_workers(2)
    try:
        await coordinator.coordinate("answer")
        await asyncio.wait_for(coordinator.task_queue.join(), timeout=2)
        assert coordinator.model_router.executions == expected
        assert all(task.status is TaskStatus.COMPLETED for task in coordinator.tasks.values())
    finally:
        await coordinator.stop_workers()


async def test_failed_task_keeps_its_terminal_outcome(coordinator):
    coordinator.model_router.failure = True
    task = await coordinator.submit_task("fail once")
    first = await coordinator.execute_task(task)
    second = await coordinator.execute_task(task)
    assert first == second == "Error: model unavailable"
    assert coordinator.model_router.executions == {"fail once": 1}
    assert task.status is TaskStatus.FAILED
    assert task.result is None
    assert task.error == "model unavailable"
    assert task.completed_at is not None


async def test_missing_agent_records_a_terminal_failure(coordinator):
    task = await coordinator.submit_task("cannot run")
    coordinator.agents.clear()
    result = await coordinator.execute_task(task)
    assert "not found" in result
    assert task.status is TaskStatus.FAILED
    assert task.error is not None
    assert task.completed_at is not None
    assert coordinator.model_router.executions == {}


async def test_cancelled_owner_cannot_be_reexecuted(coordinator):
    router = coordinator.model_router
    router.release.clear()
    task = await coordinator.submit_task("cancel once")
    owner = asyncio.create_task(coordinator.execute_task(task))
    await asyncio.wait_for(router.started.wait(), timeout=2)
    waiter = asyncio.create_task(coordinator.execute_task(task))
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    router.release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert task.status.value == "cancelled"
    assert task.completed_at is not None
    with pytest.raises(asyncio.CancelledError):
        await coordinator.execute_task(task)
    assert router.executions == {"cancel once": 1}


async def test_cancelling_waiter_does_not_cancel_owner(coordinator):
    router = coordinator.model_router
    router.release.clear()
    task = await coordinator.submit_task("keep running")
    owner = asyncio.create_task(coordinator.execute_task(task))
    await asyncio.wait_for(router.started.wait(), timeout=2)
    waiter = asyncio.create_task(coordinator.execute_task(task))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert task.status is TaskStatus.RUNNING
    router.release.set()
    assert await owner == "keep running: execution 1"
    assert task.status is TaskStatus.COMPLETED
    assert router.executions == {"keep running": 1}


async def test_stopped_worker_marks_active_task_cancelled_and_releases_queue(coordinator):
    router = coordinator.model_router
    router.release.clear()
    task = await coordinator.submit_task("worker cancellation")
    await coordinator.start_workers(1)
    await asyncio.wait_for(router.started.wait(), timeout=2)
    await coordinator.stop_workers()
    assert task.status.value == "cancelled"
    await asyncio.wait_for(coordinator.task_queue.join(), timeout=2)


async def test_coordinate_reports_failed_subtask_as_error(coordinator):
    coordinator.model_router.plan = "SUBTASKS:\n1. failing subtask"
    coordinator.model_router.failure = True
    result = await coordinator.coordinate("complete the work")
    assert result["results"] == [{"error": "model unavailable"}]


async def test_workers_continue_after_dequeuing_a_cancelled_task(coordinator):
    router = coordinator.model_router
    router.release.clear()
    task = await coordinator.submit_task("cancelled before worker")
    owner = asyncio.create_task(coordinator.execute_task(task))
    await asyncio.wait_for(router.started.wait(), timeout=2)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    router.release.set()
    next_task = await coordinator.submit_task("next task")
    await coordinator.start_workers(1)
    try:
        await asyncio.wait_for(coordinator.task_queue.join(), timeout=2)
        assert next_task.status is TaskStatus.COMPLETED
        assert router.executions == {"cancelled before worker": 1, "next task": 1}
    finally:
        await coordinator.stop_workers()
