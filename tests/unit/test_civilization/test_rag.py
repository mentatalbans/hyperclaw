"""
Unit tests for civilization/retrieval/ — RAG, context injection, relevance ranking.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import NodeType, NodeStatus, SOP, SOPStep, Role, CivilizationNode
from civilization.retrieval.civilization_rag import CivilizationRAG, RAGResult, RAGConfig
from civilization.retrieval.context_injector import ContextInjector, InjectionMode, InjectionConfig
from civilization.retrieval.relevance_ranker import RelevanceRanker, RankingConfig, RankingFactors


class TestCivilizationRAG:
    @pytest.fixture
    def rag(self):
        return CivilizationRAG(org_id="org_test")

    @pytest.mark.asyncio
    async def test_query_returns_result(self, rag):
        result = await rag.query("how to onboard customers")
        assert isinstance(result, RAGResult)
        assert result.query == "how to onboard customers"

    @pytest.mark.asyncio
    async def test_query_by_type(self, rag):
        result = await rag.query_by_type("deployment process", NodeType.SOP)
        assert isinstance(result, RAGResult)

    @pytest.mark.asyncio
    async def test_get_sop_for_task(self, rag):
        result = await rag.get_sop_for_task("process refunds")
        assert isinstance(result, RAGResult)

    @pytest.mark.asyncio
    async def test_get_runbook_for_incident(self, rag):
        result = await rag.get_runbook_for_incident("database connection errors")
        assert isinstance(result, RAGResult)


class TestContextInjector:
    @pytest.fixture
    def injector(self):
        return ContextInjector()

    @pytest.fixture
    def sample_nodes(self):
        return [
            SOP(
                org_id="o",
                title="Customer Onboarding",
                purpose="Onboard customers",
                scope="All customers",
                steps=[
                    SOPStep(step_number=1, title="Verify", description="Verify info"),
                    SOPStep(step_number=2, title="Setup", description="Setup account"),
                ],
                roles_involved=["Manager"],
                tags=["customer", "onboarding"],
            ),
            Role(
                org_id="o",
                title="Account Manager",
                role_title="Account Manager",
                department="Sales",
                accountabilities=["Customer satisfaction"],
                responsibilities=["Manage accounts", "Handle escalations"],
                decision_authority=["Discounts up to 10%"],
                escalation_path=["Sales Director"],
                interfaces=["Support", "Billing"],
            ),
        ]

    def test_inject_summary_mode(self, injector, sample_nodes):
        config = InjectionConfig(mode=InjectionMode.SUMMARY, max_tokens=2000)
        result = injector.inject(sample_nodes, config)
        assert result.nodes_included == 2
        assert "Customer Onboarding" in result.context
        assert result.token_estimate > 0

    def test_inject_reference_mode(self, injector, sample_nodes):
        config = InjectionConfig(mode=InjectionMode.REFERENCE)
        result = injector.inject(sample_nodes, config)
        assert "sop" in result.context.lower()
        assert result.nodes_included == 2

    def test_inject_minimal_mode(self, injector, sample_nodes):
        config = InjectionConfig(mode=InjectionMode.MINIMAL)
        result = injector.inject(sample_nodes, config)
        assert result.context != ""
        # Minimal should be shorter
        assert result.token_estimate < 500

    def test_inject_full_mode(self, injector, sample_nodes):
        config = InjectionConfig(mode=InjectionMode.FULL, include_metadata=True)
        result = injector.inject(sample_nodes, config)
        assert "Steps" in result.context or "Purpose" in result.context

    def test_inject_structured_mode(self, injector, sample_nodes):
        config = InjectionConfig(mode=InjectionMode.STRUCTURED)
        result = injector.inject(sample_nodes, config)
        assert "{" in result.context  # JSON format

    def test_inject_respects_token_limit(self, injector):
        # Create many nodes
        nodes = [
            SOP(
                org_id="o",
                title=f"SOP {i}",
                purpose="Purpose " * 50,  # Long purpose
                scope="Scope",
                steps=[SOPStep(step_number=1, title="S", description="D" * 200)],
            )
            for i in range(20)
        ]
        config = InjectionConfig(mode=InjectionMode.FULL, max_tokens=500)
        result = injector.inject(nodes, config)
        assert result.truncated is True
        assert result.nodes_included < 20

    def test_inject_prioritizes_by_type(self, injector, sample_nodes):
        config = InjectionConfig(priority_types=[NodeType.ROLE])
        result = injector.inject(sample_nodes, config)
        # Role should come first
        assert result.context.index("Account Manager") < result.context.index("Customer Onboarding")

    def test_format_as_markdown(self, injector, sample_nodes):
        config = InjectionConfig(format="markdown")
        result = injector.inject(sample_nodes, config)
        assert "# " in result.context

    def test_format_as_xml(self, injector, sample_nodes):
        config = InjectionConfig(format="xml")
        result = injector.inject(sample_nodes, config)
        assert "<organizational_knowledge>" in result.context

    def test_build_system_prompt_section(self, injector, sample_nodes):
        section = injector.build_system_prompt_section(sample_nodes, "CONTEXT")
        assert "<CONTEXT>" in section
        assert "</CONTEXT>" in section


class TestRelevanceRanker:
    @pytest.fixture
    def ranker(self):
        return RelevanceRanker()

    @pytest.fixture
    def sample_nodes_with_scores(self):
        now = datetime.utcnow()
        nodes = [
            (SOP(
                org_id="o",
                title="Fresh SOP",
                purpose="P",
                scope="S",
                steps=[SOPStep(step_number=1, title="T", description="D")],
                status=NodeStatus.ACTIVE,
                tags=["deployment"],
            ), 0.9),
            (SOP(
                org_id="o",
                title="Old SOP",
                purpose="P",
                scope="S",
                steps=[SOPStep(step_number=1, title="T", description="D")],
                status=NodeStatus.DEPRECATED,
                tags=["other"],
            ), 0.85),
            (Role(
                org_id="o",
                title="Role",
                role_title="Manager",
                department="Eng",
                accountabilities=[],
                responsibilities=[],
                decision_authority=[],
                escalation_path=[],
                interfaces=[],
            ), 0.7),
        ]
        # Make second SOP old
        nodes[1][0].updated_at = now - timedelta(days=100)
        return nodes

    def test_rank_returns_sorted_results(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(sample_nodes_with_scores, "deployment procedure")
        assert len(results) == 3
        # Should be sorted by final score
        assert results[0].rank == 1
        assert results[1].rank == 2
        assert results[2].rank == 3
        assert results[0].final_score >= results[1].final_score

    def test_rank_considers_recency(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(sample_nodes_with_scores, "procedure")
        # Fresh SOP should have higher recency score
        fresh_result = next(r for r in results if "Fresh" in r.node.title)
        old_result = next(r for r in results if "Old" in r.node.title)
        assert fresh_result.factors.recency_score > old_result.factors.recency_score

    def test_rank_considers_status(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(sample_nodes_with_scores, "procedure")
        fresh_result = next(r for r in results if "Fresh" in r.node.title)
        old_result = next(r for r in results if "Old" in r.node.title)
        assert fresh_result.factors.status_score > old_result.factors.status_score

    def test_rank_considers_type_match(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(
            sample_nodes_with_scores,
            "how to deploy",
            preferred_types=[NodeType.SOP],
        )
        sop_results = [r for r in results if r.node.node_type == NodeType.SOP]
        role_results = [r for r in results if r.node.node_type == NodeType.ROLE]
        assert sop_results[0].factors.type_match_score > role_results[0].factors.type_match_score

    def test_rank_considers_tag_match(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(
            sample_nodes_with_scores,
            "deployment",
            query_tags=["deployment"],
        )
        tagged_result = next(r for r in results if "deployment" in r.node.tags)
        untagged_result = next(r for r in results if "deployment" not in r.node.tags)
        assert tagged_result.factors.tag_match_score > untagged_result.factors.tag_match_score

    def test_rank_generates_explanation(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(sample_nodes_with_scores, "test")
        assert all(r.explanation is not None for r in results)

    def test_boost_node(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(sample_nodes_with_scores, "test")
        last_node_id = results[-1].node.id
        original_rank = results[-1].rank

        boosted = ranker.boost_node(results, last_node_id, boost_factor=10.0)
        new_result = next(r for r in boosted if r.node.id == last_node_id)
        assert new_result.rank < original_rank

    def test_filter_by_threshold(self, ranker, sample_nodes_with_scores):
        results = ranker.rank(sample_nodes_with_scores, "test")
        filtered = ranker.filter_by_threshold(results, min_score=0.5)
        assert all(r.final_score >= 0.5 for r in filtered)

    def test_detect_type_intent(self, ranker):
        # Test that query intent detection works
        config = RankingConfig()
        ranker_with_config = RelevanceRanker(config)

        # "how to" should prefer SOPs
        assert NodeType.SOP in ranker_with_config._detect_type_intent("how to deploy")
        # "who is" should prefer Person/Role
        assert NodeType.PERSON in ranker_with_config._detect_type_intent("who is responsible")
