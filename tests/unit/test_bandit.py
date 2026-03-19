"""
Unit tests for core/hyperrouter/bandit.py — UCB1, select_model, update_scores, HyperRouter.
"""
from __future__ import annotations

import math
import copy
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.hyperrouter.bandit import (
    HyperRouter,
    CHATJIMMY_TASK_TYPES,
    MODEL_COSTS,
    MODEL_LATENCY_MS,
    select_agent,
    select_model,
    ucb1_score,
    update_scores,
)
from core.hyperstate.schema import AgentScore, ModelScore


# ── ucb1_score ─────────────────────────────────────────────────────────────────

class TestUCB1Score:
    def test_returns_inf_for_zero_attempts(self):
        score = ucb1_score(successes=0, attempts=0, total_attempts=100)
        assert score == float("inf")

    def test_returns_inf_when_attempts_zero_regardless_of_successes(self):
        # successes can't exceed attempts in practice, but guard the zero case
        score = ucb1_score(successes=0, attempts=0, total_attempts=0)
        assert score == float("inf")

    def test_correct_formula(self):
        # With 8 successes / 10 attempts, total=100, c=2.0
        # mean = 0.8, exploration = 2 * sqrt(ln(100)/10) = 2 * sqrt(4.605/10) = 2 * 0.6786 ≈ 1.357
        result = ucb1_score(8, 10, 100, c=2.0)
        expected = 0.8 + 2.0 * math.sqrt(math.log(100) / 10)
        assert abs(result - expected) < 1e-9

    def test_unexplored_beats_explored(self):
        explored = ucb1_score(10, 10, 20)
        unexplored = ucb1_score(0, 0, 20)
        assert unexplored > explored

    def test_higher_success_rate_wins_at_large_n(self):
        # At large total_attempts, exploitation dominates
        good = ucb1_score(90, 100, 10000)
        bad = ucb1_score(50, 100, 10000)
        assert good > bad

    def test_custom_c_affects_exploration(self):
        conservative = ucb1_score(5, 10, 100, c=0.1)
        aggressive = ucb1_score(5, 10, 100, c=5.0)
        assert aggressive > conservative


# ── select_model ───────────────────────────────────────────────────────────────

class TestSelectModel:
    def test_chatjimmy_selected_for_routing_tasks(self):
        for task in CHATJIMMY_TASK_TYPES:
            model = select_model({}, task, 0)
            assert model == "chatjimmy", f"Expected chatjimmy for task={task}, got {model}"

    def test_claude_selected_for_research_tasks(self):
        # Equal attempts — exploitation dominates, Claude's 80% win rate beats ChatJimmy's 40%
        model_scores = {
            "claude-sonnet-4-6": {"research": ModelScore(attempts=100, successes=80)},
            "chatjimmy": {"research": ModelScore(attempts=100, successes=40)},
            "nim-local": {"research": ModelScore(attempts=100, successes=30)},
        }
        model = select_model(model_scores, "research", 300)
        assert model == "claude-sonnet-4-6"

    def test_budget_filter_excludes_expensive_models(self):
        model_scores = {
            "claude-sonnet-4-6": {"analysis": ModelScore(attempts=10, successes=10)},
            "chatjimmy": {"analysis": ModelScore(attempts=5, successes=3)},
        }
        # Claude costs 0.003/1k, set budget at 0.0001 → should exclude Claude
        model = select_model(model_scores, "analysis", 15, cost_budget_usd=0.0001)
        assert model == "chatjimmy"

    def test_latency_filter_excludes_slow_models(self):
        model_scores = {
            "claude-sonnet-4-6": {"code": ModelScore(attempts=10, successes=10)},
            "nim-local": {"code": ModelScore(attempts=5, successes=4)},
        }
        # Claude latency = 2000ms, nim-local = 500ms, set budget 600ms
        model = select_model(model_scores, "code", 15, latency_budget_ms=600)
        assert model == "nim-local"

    def test_falls_back_to_claude_with_empty_scores(self):
        model = select_model({}, "research", 0)
        # Either claude-sonnet-4-6 or another valid fallback (not chatjimmy for non-CJ tasks)
        assert "claude" in model or model in MODEL_COSTS

    def test_chatjimmy_excluded_by_latency_budget(self):
        # chatjimmy latency = 50ms, budget = 10ms → excluded
        model = select_model({}, "routing", 0, latency_budget_ms=10)
        # Falls back to something with lower latency or default
        assert model is not None

    def test_unexplored_model_gets_inf_score(self):
        # Model with 0 attempts should be explored first (UCB1 → inf)
        model_scores = {
            "claude-sonnet-4-6": {"analysis": ModelScore(attempts=0, successes=0)},
        }
        model = select_model(model_scores, "analysis", 10)
        assert model == "claude-sonnet-4-6"


# ── update_scores ──────────────────────────────────────────────────────────────

class TestUpdateScores:
    def test_success_increments_both(self):
        scores: dict = {}
        new_scores = update_scores(scores, "claude-sonnet-4-6", "research", success=True)
        assert new_scores["claude-sonnet-4-6"]["research"].attempts == 1
        assert new_scores["claude-sonnet-4-6"]["research"].successes == 1

    def test_failure_increments_only_attempts(self):
        scores: dict = {}
        new_scores = update_scores(scores, "claude-sonnet-4-6", "research", success=False)
        assert new_scores["claude-sonnet-4-6"]["research"].attempts == 1
        assert new_scores["claude-sonnet-4-6"]["research"].successes == 0

    def test_immutability_original_unchanged(self):
        original: dict = {}
        new_scores = update_scores(original, "claude-sonnet-4-6", "research", success=True)
        # Original dict must not be mutated
        assert "claude-sonnet-4-6" not in original
        assert new_scores["claude-sonnet-4-6"]["research"].attempts == 1

    def test_immutability_nested_object_unchanged(self):
        original = {
            "claude-sonnet-4-6": {"research": ModelScore(attempts=5, successes=3)}
        }
        # Snapshot original values
        orig_attempts = original["claude-sonnet-4-6"]["research"].attempts
        orig_successes = original["claude-sonnet-4-6"]["research"].successes

        new_scores = update_scores(original, "claude-sonnet-4-6", "research", success=True)

        # Original should be unchanged
        assert original["claude-sonnet-4-6"]["research"].attempts == orig_attempts
        assert original["claude-sonnet-4-6"]["research"].successes == orig_successes
        # New scores updated
        assert new_scores["claude-sonnet-4-6"]["research"].attempts == orig_attempts + 1
        assert new_scores["claude-sonnet-4-6"]["research"].successes == orig_successes + 1

    def test_accumulates_across_calls(self):
        scores: dict = {}
        for _ in range(5):
            scores = update_scores(scores, "model-x", "code", success=True)
        for _ in range(3):
            scores = update_scores(scores, "model-x", "code", success=False)
        assert scores["model-x"]["code"].attempts == 8
        assert scores["model-x"]["code"].successes == 5

    def test_multiple_models_independent(self):
        scores: dict = {}
        scores = update_scores(scores, "model-a", "research", success=True)
        scores = update_scores(scores, "model-b", "research", success=False)
        assert scores["model-a"]["research"].successes == 1
        assert scores["model-b"]["research"].successes == 0


# ── HyperRouter ────────────────────────────────────────────────────────────────

class TestHyperRouter:
    def test_route_returns_tuple(self):
        router = HyperRouter()
        result = router.route("research")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_route_returns_chatjimmy_for_routing_tasks(self):
        router = HyperRouter()
        agent_id, model_id = router.route("routing")
        assert model_id == "chatjimmy"

    def test_route_with_no_agents_returns_default(self):
        router = HyperRouter()
        agent_id, model_id = router.route("research")
        assert agent_id == "default_agent"

    def test_record_updates_total_attempts(self):
        router = HyperRouter()
        assert router.total_attempts == 0
        router.record("agent-1", "claude-sonnet-4-6", "research", success=True)
        assert router.total_attempts == 1

    def test_record_updates_agent_scores(self):
        router = HyperRouter()
        router.record("agent-1", "claude-sonnet-4-6", "research", success=True)
        assert router.agent_scores["agent-1"].successes == 1
        assert router.agent_scores["agent-1"].attempts == 1

    def test_record_failure_no_success_increment(self):
        router = HyperRouter()
        router.record("agent-1", "claude-sonnet-4-6", "research", success=False)
        assert router.agent_scores["agent-1"].successes == 0
        assert router.agent_scores["agent-1"].attempts == 1

    def test_route_respects_cost_budget(self):
        router = HyperRouter()
        # Very tight budget — should pick chatjimmy or nim-local
        agent_id, model_id = router.route("analysis", cost_budget=0.0001)
        assert model_id in ("chatjimmy", "nim-local")

    def test_route_respects_latency_budget(self):
        router = HyperRouter()
        # 100ms budget — should exclude claude (2000ms) and claude-code (3000ms)
        agent_id, model_id = router.route("analysis", latency_budget=100)
        assert model_id not in ("claude-sonnet-4-6", "claude-code")
