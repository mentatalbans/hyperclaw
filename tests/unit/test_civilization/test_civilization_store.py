"""
Unit tests for civilization/store.py — CivilizationStore operations.
"""
from __future__ import annotations

import pytest
from uuid import uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import NodeType, NodeStatus, CivilizationNode, SOP, SOPStep
from civilization.store import CivilizationStore


class TestCivilizationStoreNoPool:
    """Tests for CivilizationStore without a database pool (no-DB mode)."""

    @pytest.fixture
    def store(self):
        return CivilizationStore(pool=None)

    @pytest.mark.asyncio
    async def test_save_returns_node(self, store):
        node = CivilizationNode(
            org_id="org_test",
            node_type=NodeType.KNOWLEDGE_ARTICLE,
            title="Test Node",
        )
        result = await store.save(node)
        assert result.id == node.id
        assert result.title == "Test Node"

    @pytest.mark.asyncio
    async def test_get_returns_none(self, store):
        result = await store.get(uuid4(), "org_test")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_type_returns_empty(self, store):
        result = await store.list_by_type("org_test", NodeType.SOP)
        assert result == []

    @pytest.mark.asyncio
    async def test_list_all_returns_empty(self, store):
        result = await store.list_all("org_test")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_by_embedding_returns_empty(self, store):
        result = await store.search_by_embedding("org_test", [0.0] * 1536)
        assert result == []

    @pytest.mark.asyncio
    async def test_search_by_tags_returns_empty(self, store):
        result = await store.search_by_tags("org_test", ["tag1", "tag2"])
        assert result == []

    @pytest.mark.asyncio
    async def test_count_by_type_returns_empty(self, store):
        result = await store.count_by_type("org_test")
        assert result == {}

    @pytest.mark.asyncio
    async def test_delete_returns_true(self, store):
        result = await store.delete(uuid4(), "org_test")
        assert result is True

    @pytest.mark.asyncio
    async def test_update_status_returns_true(self, store):
        result = await store.update_status(uuid4(), "org_test", NodeStatus.ARCHIVED)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_embedding_returns_true(self, store):
        result = await store.update_embedding(uuid4(), "org_test", [0.0] * 1536)
        assert result is True


class TestCivilizationStoreRowConversion:
    """Tests for row-to-node conversion logic."""

    @pytest.fixture
    def store(self):
        return CivilizationStore(pool=None)

    def test_row_to_node_basic(self, store):
        import json
        row = {
            "id": uuid4(),
            "org_id": "org_test",
            "node_type": "knowledge_article",
            "title": "Test",
            "status": "active",
            "version": "1.0.0",
            "content": json.dumps({
                "node_type": "knowledge_article",
                "title": "Test",
                "org_id": "org_test",
                "status": "active",
            }),
        }
        node = store._row_to_node(row)
        assert isinstance(node, CivilizationNode)
        assert node.id == row["id"]
        assert node.org_id == "org_test"

    def test_row_to_node_with_dict_content(self, store):
        row = {
            "id": uuid4(),
            "org_id": "org_test",
            "node_type": "sop",
            "title": "Test SOP",
            "status": "active",
            "version": "1.0.0",
            "content": {
                "node_type": "sop",
                "title": "Test SOP",
                "org_id": "org_test",
                "status": "active",
            },
        }
        node = store._row_to_node(row)
        assert node.title == "Test SOP"


class TestCivilizationStoreEdgeCases:
    """Edge case tests for CivilizationStore."""

    @pytest.fixture
    def store(self):
        return CivilizationStore(pool=None)

    @pytest.mark.asyncio
    async def test_save_sop_with_steps(self, store):
        sop = SOP(
            org_id="org_test",
            title="Multi-Step SOP",
            purpose="Testing",
            scope="All",
            steps=[
                SOPStep(step_number=1, title="First", description="Do first"),
                SOPStep(step_number=2, title="Second", description="Do second"),
            ],
            roles_involved=["Manager", "Engineer"],
            tools_required=["Jira", "Git"],
        )
        result = await store.save(sop)
        assert result.id == sop.id

    @pytest.mark.asyncio
    async def test_save_node_with_embedding(self, store):
        node = CivilizationNode(
            org_id="org_test",
            node_type=NodeType.KNOWLEDGE_ARTICLE,
            title="Embedded Node",
            embedding=[0.1] * 1536,
        )
        result = await store.save(node)
        assert result.embedding is not None
        assert len(result.embedding) == 1536

    @pytest.mark.asyncio
    async def test_save_node_with_metadata(self, store):
        node = CivilizationNode(
            org_id="org_test",
            node_type=NodeType.KNOWLEDGE_ARTICLE,
            title="Node with Metadata",
            metadata={
                "custom_field": "value",
                "nested": {"key": "val"},
                "list": [1, 2, 3],
            },
        )
        result = await store.save(node)
        assert result.metadata["custom_field"] == "value"

    @pytest.mark.asyncio
    async def test_save_node_with_tags(self, store):
        node = CivilizationNode(
            org_id="org_test",
            node_type=NodeType.SOP,
            title="Tagged Node",
            tags=["urgent", "hr", "onboarding"],
        )
        result = await store.save(node)
        assert len(result.tags) == 3
        assert "urgent" in result.tags

    @pytest.mark.asyncio
    async def test_search_by_owner_returns_empty(self, store):
        result = await store.search_by_owner("org_test", "user_123")
        assert result == []
