"""
StateManager — lifecycle management for HyperState objects.
Wraps store operations with business logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from .certifier import Certifier
from .schema import (
    Domain,
    ExperimentEntry,
    HyperState,
    Task,
    TaskType,
)
from .store import HyperStateStore


class StateManager:
    """
    High-level API for creating, updating, and managing HyperState lifecycles.
    Uses asyncpg pool internally via HyperStateStore.
    """

    def __init__(self, store: HyperStateStore) -> None:
        self._store = store
        self._certifier = Certifier()

    async def create_state(
        self,
        goal: str,
        domain: Domain,
        task_type: TaskType,
        constraints: Optional[list[str]] = None,
    ) -> HyperState:
        """Create and persist a new HyperState."""
        state = HyperState(
            domain=domain,
            task=Task(
                goal=goal,
                task_type=task_type,
                constraints=constraints or [],
            ),
        )
        await self._store.save_state(state)
        return state

    async def update_state(
        self,
        state_id: UUID,
        updates: dict[str, Any],
    ) -> HyperState:
        """Apply a dict of field updates to a HyperState and persist it."""
        state = await self._store.load_state(state_id)
        for key, value in updates.items():
            if hasattr(state, key):
                setattr(state, key, value)
        state._bump_version()
        await self._store.save_state(state)
        return state

    async def add_experiment(
        self,
        state_id: UUID,
        entry: ExperimentEntry,
    ) -> HyperState:
        """Append an ExperimentEntry to a HyperState's experiment log."""
        state = await self._store.load_state(state_id)
        state.experiment_log.append(entry)
        state._bump_version()
        await self._store.save_state(state)
        return state

    async def certify_method(
        self,
        state_id: UUID,
        method_id: str,
        test_trace: str,
        result: str,
        model_used: str,
    ) -> HyperState:
        """
        Certify a method on a HyperState. Validates via Certifier then
        calls HyperState.certify_method() to record the CertifiedMethod.
        Raises CertificationError if validation fails.
        """
        state = await self._store.load_state(state_id)
        # Will raise CertificationError if validation fails
        state.certify_method(
            method_id=method_id,
            test_trace=test_trace,
            result=result,
            model_used=model_used,
        )
        await self._store.save_state(state)
        return state

    async def archive_state(self, state_id: UUID) -> None:
        """Archive a HyperState (soft delete)."""
        await self._store.archive_state(state_id)
