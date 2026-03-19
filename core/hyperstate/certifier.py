"""
HyperClaw Certifier — validates ExperimentEntries before promoting to CertifiedMethod.
ChatJimmy outputs must always pass through Claude verification before reaching this stage.
"""
from __future__ import annotations

from .schema import CertifiedMethod, ExperimentEntry


class CertificationError(Exception):
    """Raised when an ExperimentEntry fails certification criteria."""
    pass


class Certifier:
    """
    Validates ExperimentEntry objects and produces CertifiedMethod records.

    Certification rules:
    1. test_trace must be non-empty
    2. result must be non-empty
    3. certified flag must be True on the entry (caller's responsibility to set it
       after human/Claude verification)
    """

    def certify(self, entry: ExperimentEntry) -> CertifiedMethod:
        """
        Validate an ExperimentEntry and return a CertifiedMethod.

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

        return CertifiedMethod(
            method_id=entry.method,
            model_used=entry.model_used,
            performance={
                "cost_usd": entry.cost_usd,
                "latency_ms": entry.latency_ms,
                "test_trace_length": len(entry.test_trace),
                "result_length": len(entry.result),
            },
        )

    def validate_batch(
        self,
        entries: list[ExperimentEntry],
    ) -> tuple[list[CertifiedMethod], list[CertificationError]]:
        """
        Attempt to certify a batch of entries. Returns (certified, errors).
        Does not raise — all errors are collected and returned.
        """
        certified: list[CertifiedMethod] = []
        errors: list[CertificationError] = []

        for entry in entries:
            try:
                cm = self.certify(entry)
                certified.append(cm)
            except CertificationError as e:
                errors.append(e)

        return certified, errors
