"""
Phase 5+6 unit tests — BidProtocol, AgentRegistry, RecursiveGrowthEngine, BaseAgent, NEXUS.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from swarm.bid_protocol import (
    BidCoordinator, BidRequest, Bid, Award, AgentBidder, _bid_score,
)
from core.hyperrouter.bandit import HyperRouter
from core.hyperstate.schema import HyperState, Task, AgentScore, ModelScore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_deps():
    model_router = MagicMock()
    model_router.call = AsyncMock(return_value="mock response")
    state_manager = MagicMock()
    state_manager.create_state = AsyncMock()
    state_manager.update_state = AsyncMock()
    causal_graph = MagicMock()
    causal_graph.add_node = AsyncMock(return_value=uuid.uuid4())
    causal_graph.write_certified_method = AsyncMock(return_value=(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()))
    hyper_shield = MagicMock()
    hyper_shield.check_inference_call = AsyncMock(return_value=True)
    return model_router, state_manager, causal_graph, hyper_shield


def _make_state(task_type="research"):
    return HyperState(domain="business", task=Task(goal="test", task_type=task_type))


def _make_bid(agent_id="agent-1", confidence=0.8, cost=0.01, eta=2.0, request_id=None):
    return Bid(
        request_id=request_id or uuid.uuid4(),
        agent_id=agent_id,
        model_id="claude-sonnet-4-6",
        confidence=confidence,
        eta_seconds=eta,
        estimated_cost_usd=cost,
        rationale="test bid",
    )


# ── BidProtocol ───────────────────────────────────────────────────────────────

class TestBidScore:
    def test_perfect_bid_score(self):
        req = BidRequest(cost_budget_usd=1.0)
        bid = _make_bid(confidence=1.0, cost=0.0, eta=0.0)
        score = _bid_score(bid, req)
        assert abs(score - 1.0) < 0.01

    def test_worst_bid_score(self):
        req = BidRequest(cost_budget_usd=1.0)
        bid = _make_bid(confidence=0.0, cost=1.0, eta=30.0)
        score = _bid_score(bid, req)
        assert abs(score - 0.0) < 0.01

    def test_scoring_weights(self):
        req = BidRequest(cost_budget_usd=1.0)
        # High confidence, high cost, slow
        bid = _make_bid(confidence=1.0, cost=1.0, eta=30.0)
        score = _bid_score(bid, req)
        # Only confidence contributes: 1.0 * 0.6 = 0.6
        assert abs(score - 0.6) < 0.01


class TestBidCoordinator:
    @pytest.mark.asyncio
    async def test_broadcast_returns_request(self):
        router = HyperRouter()
        coord = BidCoordinator(router, ["agent-1"])
        req = await coord.broadcast(
            subtask_id=uuid.uuid4(),
            task_type="research",
            domain="business",
            context_summary="Test task",
        )
        assert isinstance(req, BidRequest)
        assert req.task_type == "research"

    @pytest.mark.asyncio
    async def test_award_selects_highest_score(self):
        router = HyperRouter()
        coord = BidCoordinator(router, ["agent-1", "agent-2"])
        req = await coord.broadcast(uuid.uuid4(), "research", "business", "test")

        bid_high = _make_bid("agent-1", confidence=0.9, cost=0.01, eta=1.0, request_id=req.request_id)
        bid_low = _make_bid("agent-2", confidence=0.3, cost=0.01, eta=1.0, request_id=req.request_id)
        award = await coord.award(req, [bid_low, bid_high])
        assert award.winning_agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_award_tie_broken_by_cost(self):
        router = HyperRouter()
        coord = BidCoordinator(router, ["agent-1", "agent-2"])
        req = await coord.broadcast(uuid.uuid4(), "research", "business", "test")

        bid_cheap = _make_bid("agent-1", confidence=0.8, cost=0.001, eta=2.0, request_id=req.request_id)
        bid_expensive = _make_bid("agent-2", confidence=0.8, cost=0.01, eta=2.0, request_id=req.request_id)
        award = await coord.award(req, [bid_cheap, bid_expensive])
        assert award.winning_agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_award_fallback_on_no_bids(self):
        router = HyperRouter()
        coord = BidCoordinator(router, ["agent-1"])
        req = await coord.broadcast(uuid.uuid4(), "routing", "business", "test")
        award = await coord.award(req, [])
        assert isinstance(award, Award)
        assert award.competing_bids == 0

    @pytest.mark.asyncio
    async def test_negotiate_full_pipeline(self):
        router = HyperRouter()
        coord = BidCoordinator(router, ["agent-1"])
        award = await coord.negotiate(uuid.uuid4(), "research", "business", "test task")
        assert isinstance(award, Award)

    @pytest.mark.asyncio
    async def test_award_records_competing_bids(self):
        router = HyperRouter()
        coord = BidCoordinator(router, ["a1", "a2", "a3"])
        req = await coord.broadcast(uuid.uuid4(), "research", "business", "test")
        bids = [
            _make_bid(f"agent-{i}", confidence=0.5, request_id=req.request_id)
            for i in range(3)
        ]
        award = await coord.award(req, bids)
        assert award.competing_bids == 3


class TestAgentBidder:
    @pytest.mark.asyncio
    async def test_confidence_clamped_to_1(self):
        bidder = AgentBidder()
        bidder.agent_id = "test-agent"
        bidder.active_tasks = 0
        req = BidRequest(task_type="research")
        bid = await bidder.compute_bid(req, {})
        assert 0.0 <= bid.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_load_penalty_applied(self):
        bidder = AgentBidder()
        bidder.agent_id = "test-agent"
        bidder.active_tasks = 4  # > 3 → penalty
        req = BidRequest(task_type="research")
        bid_loaded = await bidder.compute_bid(req, {})

        bidder.active_tasks = 0
        bid_free = await bidder.compute_bid(req, {})

        assert bid_loaded.confidence < bid_free.confidence

    @pytest.mark.asyncio
    async def test_confidence_never_negative(self):
        bidder = AgentBidder()
        bidder.agent_id = "test-agent"
        bidder.active_tasks = 100
        req = BidRequest(task_type="research")
        bid = await bidder.compute_bid(req, {})
        assert bid.confidence >= 0.0


class TestBaseAgent:
    @pytest.mark.asyncio
    async def test_log_completion_updates_agent_scores(self):
        from swarm.agents.base import BaseAgent
        deps = _make_deps()
        agent = BaseAgent(*deps)
        agent.agent_id = "TEST"
        state = _make_state()

        await agent.log_completion(state, "result", "claude-sonnet-4-6", success=True)

        assert "TEST" in state.agent_scores
        assert state.agent_scores["TEST"].attempts == 1
        assert state.agent_scores["TEST"].successes == 1

    @pytest.mark.asyncio
    async def test_log_completion_failure_no_success_increment(self):
        from swarm.agents.base import BaseAgent
        deps = _make_deps()
        agent = BaseAgent(*deps)
        agent.agent_id = "TEST"
        state = _make_state()

        await agent.log_completion(state, "result", "claude-sonnet-4-6", success=False)
        assert state.agent_scores["TEST"].attempts == 1
        assert state.agent_scores["TEST"].successes == 0

    @pytest.mark.asyncio
    async def test_log_completion_updates_model_scores(self):
        from swarm.agents.base import BaseAgent
        deps = _make_deps()
        agent = BaseAgent(*deps)
        agent.agent_id = "TEST"
        state = _make_state(task_type="research")

        await agent.log_completion(state, "result", "claude-sonnet-4-6", success=True)
        assert "claude-sonnet-4-6" in state.model_scores
        assert "research" in state.model_scores["claude-sonnet-4-6"]

    @pytest.mark.asyncio
    async def test_log_completion_increments_version(self):
        from swarm.agents.base import BaseAgent
        deps = _make_deps()
        agent = BaseAgent(*deps)
        agent.agent_id = "TEST"
        state = _make_state()
        initial_version = state.state_version

        await agent.log_completion(state, "result", "claude-sonnet-4-6", success=True)
        assert state.state_version == initial_version + 1


class TestAgentRegistry:
    def test_build_default_registers_correct_count(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        all_agents = registry.list_all()
        assert len(all_agents) == 36  # Core agent swarm (after dedup)

    def test_no_duplicate_agent_ids(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        ids = [a.agent_id for a in registry.list_all()]
        assert len(ids) == len(set(ids))

    def test_all_expected_agents_present(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        expected = {
            # Personal
            "ATLAS", "MIDAS", "VITALS", "NOURISH", "NAVIGATOR", "HEARTH",
            # Business
            "STRATEGOS", "HERALD", "PIPELINE", "LEDGER", "COUNSEL", "TALENT",
            "NEXUS", "OPS", "REVENUE", "SOVEREIGN",
            # Scientific
            "MEDICUS", "COSMOS", "GAIA", "ORACLE", "SCRIBE",
            # Creative
            "AUTHOR", "LENS",
            # Recursive
            "SCOUT", "ALCHEMIST", "CALIBRATOR",
            # Tech & security
            "AEGIS", "CIPHER", "BRIDGE", "FORGE",
            # Comms
            "ENVOY", "ECHO", "PULSE",
            # Talent
            "DEAL", "ROSTER", "STAGE",
        }
        registered = {a.agent_id for a in registry.list_all()}
        assert expected == registered

    def test_list_by_domain(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        personal = registry.list_by_domain("personal")
        assert len(personal) == 6
        assert all(a.domain == "personal" for a in personal)

    def test_list_by_task_type(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        finance_agents = registry.list_by_task_type("finance")
        ids = {a.agent_id for a in finance_agents}
        assert "MIDAS" in ids
        assert "LEDGER" in ids

    def test_get_registered_agent(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        atlas = registry.get("ATLAS")
        assert atlas.agent_id == "ATLAS"

    def test_get_missing_agent_raises(self):
        from swarm.registry import AgentRegistry
        deps = _make_deps()
        registry = AgentRegistry.build_default(*deps)
        with pytest.raises(KeyError):
            registry.get("NONEXISTENT")


class TestSpecialistAgents:
    @pytest.mark.asyncio
    async def test_vitals_appends_disclaimer(self):
        from swarm.agents.personal.vitals import VitalsAgent, MEDICAL_DISCLAIMER
        deps = _make_deps()
        agent = VitalsAgent(*deps)
        state = _make_state("health")
        result = await agent.run("Check symptoms", state, {})
        assert MEDICAL_DISCLAIMER in result

    @pytest.mark.asyncio
    async def test_medicus_appends_disclaimer(self):
        from swarm.agents.scientific.medicus import MedicusAgent, MEDICAL_DISCLAIMER
        deps = _make_deps()
        agent = MedicusAgent(*deps)
        state = _make_state("research")
        result = await agent.run("Research drug interaction", state, {})
        assert MEDICAL_DISCLAIMER in result

    @pytest.mark.asyncio
    async def test_counsel_appends_disclaimer(self):
        from swarm.agents.business.counsel import CounselAgent, LEGAL_DISCLAIMER
        deps = _make_deps()
        agent = CounselAgent(*deps)
        state = _make_state("research")
        result = await agent.run("Review contract clause", state, {})
        assert LEGAL_DISCLAIMER in result

    @pytest.mark.asyncio
    async def test_strategos_uses_claude(self):
        from swarm.agents.business.strategos import StrategosAgent
        deps = _make_deps()
        agent = StrategosAgent(*deps)
        assert agent.preferred_model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_hearth_uses_chatjimmy(self):
        from swarm.agents.personal.hearth import HearthAgent
        deps = _make_deps()
        agent = HearthAgent(*deps)
        assert agent.preferred_model == "chatjimmy"

    @pytest.mark.asyncio
    async def test_atlas_run_returns_string(self):
        from swarm.agents.personal.atlas import AtlasAgent
        deps = _make_deps()
        agent = AtlasAgent(*deps)
        state = _make_state("planning")
        result = await agent.run("Plan my week", state, {})
        assert isinstance(result, str)
        assert len(result) > 0


class TestRecursiveGrowthEngine:
    @pytest.mark.asyncio
    async def test_run_once_returns_correct_keys(self):
        from recursive.discovery_loop import RecursiveGrowthEngine

        scout = MagicMock()
        scout.sweep = AsyncMock(return_value=[])
        alchemist = MagicMock()
        alchemist.run = AsyncMock(return_value="ALCHEMIST: validated=0, skills_added=0")
        calibrator = MagicMock()
        calibrator.run = AsyncMock(return_value="CALIBRATOR: No inefficiencies.")
        causal_graph = MagicMock()
        causal_graph.add_node = AsyncMock(return_value=uuid.uuid4())

        engine = RecursiveGrowthEngine(scout, alchemist, calibrator, causal_graph)
        summary = await engine.run_once(["scientific", "recursive"])

        assert "discoveries_found" in summary
        assert "actionable" in summary
        assert "skills_added" in summary
        assert "optimizations_proposed" in summary
        assert "timestamp" in summary

    @pytest.mark.asyncio
    async def test_run_once_calls_scout(self):
        from recursive.discovery_loop import RecursiveGrowthEngine

        scout = MagicMock()
        scout.sweep = AsyncMock(return_value=[])
        alchemist = MagicMock()
        alchemist.run = AsyncMock(return_value="ALCHEMIST: validated=0, skills_added=0")
        calibrator = MagicMock()
        calibrator.run = AsyncMock(return_value="ok")
        causal_graph = MagicMock()
        causal_graph.add_node = AsyncMock(return_value=uuid.uuid4())

        engine = RecursiveGrowthEngine(scout, alchemist, calibrator, causal_graph)
        await engine.run_once(["scientific"])
        scout.sweep.assert_called_once_with(["scientific"])

    @pytest.mark.asyncio
    async def test_run_once_calls_all_three_agents(self):
        from recursive.discovery_loop import RecursiveGrowthEngine
        from swarm.agents.recursive.scout import Discovery

        d = Discovery(actionable=True, relevance_score=0.8, source_name="test", title="Test")
        scout = MagicMock()
        scout.sweep = AsyncMock(return_value=[d])
        alchemist = MagicMock()
        alchemist.run = AsyncMock(return_value="ALCHEMIST: validated=1, skills_added=1")
        calibrator = MagicMock()
        calibrator.run = AsyncMock(return_value="CALIBRATOR: No inefficiencies.")
        causal_graph = MagicMock()
        causal_graph.add_node = AsyncMock(return_value=uuid.uuid4())

        engine = RecursiveGrowthEngine(scout, alchemist, calibrator, causal_graph)
        await engine.run_once(["recursive"])

        scout.sweep.assert_called_once()
        alchemist.run.assert_called_once()
        calibrator.run.assert_called_once()


class TestOptimizationLoop:
    @pytest.mark.asyncio
    async def test_handles_empty_messages_gracefully(self):
        from recursive.optimization_loop import OptimizationLoop

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        router = HyperRouter()
        loop = OptimizationLoop()
        report = await loop.analyze(pool, router)

        assert report["messages_analyzed"] == 0
        assert "recommendations" in report
        assert len(report["recommendations"]) > 0

    @pytest.mark.asyncio
    async def test_report_has_required_keys(self):
        from recursive.optimization_loop import OptimizationLoop

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        router = HyperRouter()
        loop = OptimizationLoop()
        report = await loop.analyze(pool, router)

        required_keys = {
            "messages_analyzed", "expensive_model_overuse",
            "underperforming_agents", "low_cert_domains",
            "recommendations", "timestamp",
        }
        assert required_keys.issubset(report.keys())
