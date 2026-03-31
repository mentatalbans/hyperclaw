"""
Unit tests for core/hyperstate/certifier.py — Certifier and CertificationError.
"""
from __future__ import annotations

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.hyperstate.certifier import Certifier, CertificationError
from core.hyperstate.schema import ExperimentEntry


class TestCertifier:
    def setup_method(self):
        self.certifier = Certifier()

    def _good_entry(self, method: str = "test_method") -> ExperimentEntry:
        return ExperimentEntry(
            method=method,
            model_used="claude-sonnet-4-6",
            result="All assertions passed. Output: 42",
            certified=True,
            test_trace="test_foo: PASS\ntest_bar: PASS\n2 passed in 0.01s",
            cost_usd=0.001,
            latency_ms=1200.0,
        )

    def test_happy_path(self):
        entry = self._good_entry()
        cm = self.certifier.certify(entry)
        assert cm.method_id == "test_method"
        assert cm.model_used == "claude-sonnet-4-6"
        assert "cost_usd" in cm.performance

    def test_raises_on_empty_test_trace(self):
        entry = self._good_entry()
        entry.test_trace = ""
        with pytest.raises(CertificationError) as exc_info:
            self.certifier.certify(entry)
        assert "test_trace" in str(exc_info.value).lower()

    def test_raises_on_whitespace_test_trace(self):
        entry = self._good_entry()
        entry.test_trace = "   \n\t  "
        with pytest.raises(CertificationError):
            self.certifier.certify(entry)

    def test_raises_on_empty_result(self):
        entry = self._good_entry()
        entry.result = ""
        with pytest.raises(CertificationError) as exc_info:
            self.certifier.certify(entry)
        assert "result" in str(exc_info.value).lower()

    def test_raises_on_whitespace_result(self):
        entry = self._good_entry()
        entry.result = "   "
        with pytest.raises(CertificationError):
            self.certifier.certify(entry)

    def test_raises_when_not_certified(self):
        entry = self._good_entry()
        entry.certified = False
        with pytest.raises(CertificationError) as exc_info:
            self.certifier.certify(entry)
        assert "certified" in str(exc_info.value).lower()

    def test_certified_method_has_performance_metrics(self):
        entry = self._good_entry()
        cm = self.certifier.certify(entry)
        assert cm.performance["cost_usd"] == entry.cost_usd
        assert cm.performance["latency_ms"] == entry.latency_ms
        assert cm.performance["test_trace_length"] == len(entry.test_trace)

    def test_validate_batch_all_valid(self):
        entries = [self._good_entry(f"method_{i}") for i in range(3)]
        certified, errors = self.certifier.validate_batch(entries)
        assert len(certified) == 3
        assert len(errors) == 0

    def test_validate_batch_mixed(self):
        entries = [
            self._good_entry("method_0"),
            ExperimentEntry(method="bad_method", model_used="claude-sonnet-4-6",
                          result="", certified=True, test_trace="trace"),
            self._good_entry("method_2"),
        ]
        certified, errors = self.certifier.validate_batch(entries)
        assert len(certified) == 2
        assert len(errors) == 1
        assert isinstance(errors[0], CertificationError)

    def test_validate_batch_all_invalid(self):
        bad = ExperimentEntry(
            method="bad", model_used="claude-sonnet-4-6",
            result="", certified=False, test_trace="",
        )
        _, errors = self.certifier.validate_batch([bad, bad])
        assert len(errors) == 2

    def test_certification_error_is_exception(self):
        err = CertificationError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"
