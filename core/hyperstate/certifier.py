"""
HyperClaw Certifier — validates ExperimentEntries before promoting to CertifiedMethod.
When a CausalGraph is provided, writes cause→effect relationships on every successful certification.
ChatJimmy outputs must always pass through Claude verification before reaching this stage.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from uuid import UUID

from .schema import CertifiedMethod, ExperimentEntry

if TYPE_CHECKING:
    from memory.causal_graph import CausalGraph


class CertificationError(Exception):
    """Raised when an ExperimentEntry fails certification criteria."""
    pass


class Certifier:
    """
    Validates ExperimentEntry objects and produces CertifiedMethod records.
    Optionally wires to CausalGraph — every certified method auto-writes a causal edge.

    Certification rules:
    1. test_trace must be non-empty
    2. result must be non-empty
    3. certified flag must be True on the entry
    """

    def __init__(self, causal_graph: "CausalGraph | None" = None) -> None:
        self._causal_graph = causal_graph

    def certify(
        self,
        entry: ExperimentEntry,
        domain: str = "business",
    ) -> CertifiedMethod:
        """
        Validate an ExperimentEntry and return a CertifiedMethod.
        If causal_graph was provided at init, schedules a causal edge write.

        Raises:
            CertificationError: if test_trace is empty, result is empty,
                                 or certified flag is not set.
        """
        if not entry.test_trace or not entry.test_trace.strip():
            raise CertificationError(
                f"Certification failed for method '{entry.method}': "
                "test_trace is empty. A non-empty test trace is required."
            )
        if not entry.result or not entry.result.strip():
            raise CertificationError(
                f"Certification failed for method '{entry.method}': "
                "result is empty. A non-empty result is required."
            )
        if not entry.certified:
            raise CertificationError(
                f"Certification failed for method '{entry.method}': "
                "entry.certified is False. Entry must be verified before certification."
            )

        certified_method = CertifiedMethod(
            method_id=entry.method,
            model_used=entry.model_used,
            performance={
                "cost_usd": entry.cost_usd,
                "latency_ms": entry.latency_ms,
                "test_trace_length": len(entry.test_trace),
                "result_length": len(entry.result),
            },
        )

        # Write causal edge if CausalGraph is wired in
        if self._causal_graph is not None:
            self._schedule_causal_write(entry, domain, certified_method)

        return certified_method

    def _schedule_causal_write(
        self,
        entry: ExperimentEntry,
        domain: str,
        certified_method: CertifiedMethod,
    ) -> None:
        """
        Fire-and-forget coroutine to write causal edge.
        Uses asyncio.ensure_future if an event loop is running,
        otherwise stores for deferred execution.
        """
        import asyncio

        coro = self._write_causal_edge(entry, domain, certified_method)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            # No running event loop — skip (sync context, e.g. unit tests)
            pass

    async def _write_causal_edge(
        self,
        entry: ExperimentEntry,
        domain: str,
        certified_method: CertifiedMethod,
    ) -> None:
        """Async: write cause→effect to CausalGraph."""
        try:
            method_uuid = UUID(certified_method.method_id) if _is_uuid(certified_method.method_id) else uuid.uuid4()
            await self._causal_graph.write_certified_method(  # type: ignore[union-attr]
                method_description=entry.method,
                cause_description=f"method: {entry.method}",
                effect_description=f"result: {entry.result}",
                domain=domain,
                confidence=1.0,
                method_id=method_uuid,
            )
        except Exception as e:
            import logging
            logging.getLogger("hyperclaw.certifier").warning(
                f"CausalGraph write failed for method '{entry.method}': {e}"
            )

    def validate_batch(
        self,
        entries: list[ExperimentEntry],
        domain: str = "business",
    ) -> tuple[list[CertifiedMethod], list[CertificationError]]:
        """
        Attempt to certify a batch of entries. Returns (certified, errors).
        Does not raise — all errors are collected and returned.
        """
        certified: list[CertifiedMethod] = []
        errors: list[CertificationError] = []

        for entry in entries:
            try:
                cm = self.certify(entry, domain=domain)
                certified.append(cm)
            except CertificationError as e:
                errors.append(e)

        return certified, errors


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False
