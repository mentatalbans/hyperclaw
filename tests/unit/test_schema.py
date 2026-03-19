"""
Unit tests for core/hyperstate/schema.py — HyperState, Task, Hypothesis, etc.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.hyperstate.schema import (
    AgentScore,
    CertifiedMethod,
    ExperimentEntry,
    Hypothesis,
    HyperState,
    ModelScore,
    RecursiveResearch,
    Task,
)
from core.hyperstate.certifier import CertificationError


# ── HyperState instantiation ───────────────────────────────────────────────────

class TestHyperStateInstantiation:
    def test_default_instantiation(self):
        state = HyperState(domain="business", task=Task(goal="test goal"))
        assert state.state_id is not None
        assert state.domain == "business"
        assert state.task.goal == "test goal"
        assert state.state_version == 0
        assert state.hypotheses == []
        assert state.experiment_log == []
        assert state.certified_methods == []

    def test_state_id_is_unique(self):
        s1 = HyperState(domain="business", task=Task(goal="a"))
        s2 = HyperState(domain="business", task=Task(goal="b"))
        assert s1.state_id != s2.state_id

    def test_created_at_set(self):
        state = HyperState(domain="personal", task=Task(goal="health check"))
        assert isinstance(state.created_at, datetime)

    def test_all_domains_accepted(self):
        for domain in ["personal", "business", "scientific", "creative", "recursive"]:
            s = HyperState(domain=domain, task=Task(goal="x"))
            assert s.domain == domain

    def test_task_priority_range(self):
        t = Task(goal="goal", priority=0)
        assert t.priority == 0
        t2 = Task(goal="goal", priority=10)
        assert t2.priority == 10
        with pytest.raises(Exception):
            Task(goal="goal", priority=11)

    def test_hypothesis_confidence_range(self):
        h = Hypothesis(statement="test", confidence=0.0)
        assert h.confidence == 0.0
        h2 = Hypothesis(statement="test", confidence=1.0)
        assert h2.confidence == 1.0
        with pytest.raises(Exception):
            Hypothesis(statement="test", confidence=1.1)


# ── certify_method happy path ──────────────────────────────────────────────────

class TestCertifyMethod:
    def test_happy_path(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        cm = state.certify_method(
            method_id="method_001",
            test_trace="All tests passed: test_foo OK, test_bar OK",
            result="Output: success",
            model_used="claude-sonnet-4-6",
        )
        assert isinstance(cm, CertifiedMethod)
        assert cm.method_id == "method_001"
        assert cm.model_used == "claude-sonnet-4-6"
        assert len(state.certified_methods) == 1
        assert state.state_version == 1

    def test_version_increments(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        assert state.state_version == 0
        state.certify_method("m1", "trace", "result", "claude-sonnet-4-6")
        assert state.state_version == 1
        state.certify_method("m2", "trace2", "result2", "claude-sonnet-4-6")
        assert state.state_version == 2

    def test_raises_on_empty_test_trace(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        with pytest.raises(CertificationError) as exc_info:
            state.certify_method("m1", "", "result", "claude-sonnet-4-6")
        assert "test_trace" in str(exc_info.value).lower()

    def test_raises_on_whitespace_test_trace(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        with pytest.raises(CertificationError):
            state.certify_method("m1", "   ", "result", "claude-sonnet-4-6")

    def test_raises_on_empty_result(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        with pytest.raises(CertificationError) as exc_info:
            state.certify_method("m1", "trace", "", "claude-sonnet-4-6")
        assert "result" in str(exc_info.value).lower()

    def test_raises_on_whitespace_result(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        with pytest.raises(CertificationError):
            state.certify_method("m1", "trace", "   ", "claude-sonnet-4-6")


# ── get_best_model ─────────────────────────────────────────────────────────────

class TestGetBestModel:
    def test_default_when_no_data(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        assert state.get_best_model("research") == "claude-sonnet-4-6"

    def test_returns_highest_win_rate(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        state.model_scores = {
            "model-a": {"research": ModelScore(attempts=10, successes=9)},
            "model-b": {"research": ModelScore(attempts=10, successes=5)},
            "model-c": {"research": ModelScore(attempts=10, successes=7)},
        }
        assert state.get_best_model("research") == "model-a"

    def test_skips_zero_attempts(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        state.model_scores = {
            "model-a": {"code": ModelScore(attempts=0, successes=0)},
            "model-b": {"code": ModelScore(attempts=5, successes=4)},
        }
        assert state.get_best_model("code") == "model-b"

    def test_ignores_other_task_types(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        state.model_scores = {
            "model-a": {"planning": ModelScore(attempts=10, successes=10)},
            "model-b": {"research": ModelScore(attempts=10, successes=9)},
        }
        # Only model-b has "research" scores
        assert state.get_best_model("research") == "model-b"

    def test_default_when_no_matching_task_type(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        state.model_scores = {
            "model-a": {"code": ModelScore(attempts=10, successes=9)},
        }
        assert state.get_best_model("research") == "claude-sonnet-4-6"

    def test_tie_returns_first_max(self):
        state = HyperState(domain="business", task=Task(goal="test"))
        state.model_scores = {
            "model-a": {"research": ModelScore(attempts=10, successes=8)},
            "model-b": {"research": ModelScore(attempts=10, successes=8)},
        }
        best = state.get_best_model("research")
        assert best in ("model-a", "model-b")
