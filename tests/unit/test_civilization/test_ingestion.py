"""
Unit tests for civilization/ingestion/ — chunking, extraction, and embedding.
"""
from __future__ import annotations

import pytest
from uuid import uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import SOP, SOPStep, Checklist, ChecklistItem, Runbook, RunbookStep, CivilizationNode, NodeType
from civilization.ingestion.chunker import ProceduralChunker, SOPChunk, ChecklistChunk, RunbookChunk, GenericChunk
from civilization.ingestion.extractor import MetadataExtractor, EntityExtractor
from civilization.ingestion.embedder import MockEmbedder, CivilizationEmbedder


class TestProceduralChunker:
    @pytest.fixture
    def chunker(self):
        return ProceduralChunker()

    @pytest.fixture
    def sample_sop(self):
        return SOP(
            org_id="org_1",
            title="Customer Onboarding",
            purpose="Onboard new customers",
            scope="All customer types",
            steps=[
                SOPStep(step_number=1, title="Verify Info", description="Check customer details", responsible_role="Account Manager"),
                SOPStep(step_number=2, title="Setup Account", description="Create accounts in systems", tools_required=["CRM", "Billing"]),
                SOPStep(step_number=3, title="Welcome Email", description="Send onboarding email"),
            ],
            roles_involved=["Account Manager", "Support"],
        )

    def test_chunk_sop(self, chunker, sample_sop):
        chunks = chunker.chunk_sop(sample_sop)
        assert len(chunks) == 3
        assert all(isinstance(c, SOPChunk) for c in chunks)

    def test_sop_chunk_contains_context(self, chunker, sample_sop):
        chunks = chunker.chunk_sop(sample_sop)

        # First chunk has no previous step
        assert "Previous step" not in chunks[0].content
        assert "Next step" in chunks[0].content

        # Middle chunk has both
        assert "Previous step" in chunks[1].content
        assert "Next step" in chunks[1].content

        # Last chunk has no next step
        assert "Previous step" in chunks[2].content
        assert "Next step" not in chunks[2].content

    def test_sop_chunk_metadata(self, chunker, sample_sop):
        chunks = chunker.chunk_sop(sample_sop)
        assert chunks[0].sop_id == sample_sop.id
        assert chunks[0].sop_title == sample_sop.title
        assert "org_id" in chunks[0].metadata

    def test_chunk_checklist(self, chunker):
        checklist = Checklist(
            org_id="org_1",
            title="Pre-flight Checklist",
            purpose="Ensure safe deployment",
            items=[
                ChecklistItem(item_number=1, description="Run tests", required=True),
                ChecklistItem(item_number=2, description="Review changes", required=True),
            ],
        )
        chunks = chunker.chunk_checklist(checklist)
        assert len(chunks) == 2
        assert all(isinstance(c, ChecklistChunk) for c in chunks)
        assert "Required: Yes" in chunks[0].content

    def test_chunk_runbook(self, chunker):
        runbook = Runbook(
            org_id="org_1",
            title="Database Recovery",
            system="postgres",
            scenario="Database crash",
            severity="P1",
            steps=[
                RunbookStep(step_number=1, action="Check status", command="pg_isready"),
                RunbookStep(step_number=2, action="Restart service", command="systemctl restart postgres"),
            ],
        )
        chunks = chunker.chunk_runbook(runbook)
        assert len(chunks) == 2
        assert all(isinstance(c, RunbookChunk) for c in chunks)
        assert "Command:" in chunks[0].content

    def test_chunk_generic(self, chunker):
        node = CivilizationNode(
            org_id="org_1",
            node_type=NodeType.KNOWLEDGE_ARTICLE,
            title="Best Practices",
        )
        text = "This is a long document. " * 100
        chunks = chunker.chunk_generic(node, text)
        assert len(chunks) >= 1
        assert all(isinstance(c, GenericChunk) for c in chunks)


class TestMetadataExtractor:
    @pytest.fixture
    def extractor(self):
        return MetadataExtractor()

    def test_extract_title(self, extractor):
        text = "# Customer Support Guide\n\nThis guide covers..."
        metadata = extractor.extract(text)
        assert metadata.title == "Customer Support Guide"

    def test_extract_purpose(self, extractor):
        text = "Purpose: To ensure consistent customer service\n\nSteps..."
        metadata = extractor.extract(text)
        assert "consistent customer service" in metadata.purpose

    def test_extract_tools(self, extractor):
        text = "Use Jira for tracking and Slack for communication."
        metadata = extractor.extract(text)
        assert "Jira" in metadata.tools_mentioned or "Slack" in metadata.tools_mentioned

    def test_detect_document_type_sop(self, extractor):
        text = "Standard Operating Procedure\nStep 1: Do this\nStep 2: Do that"
        metadata = extractor.extract(text)
        assert metadata.document_type == "sop"

    def test_detect_document_type_checklist(self, extractor):
        text = "Pre-launch Checklist\n[ ] Item 1\n[ ] Item 2"
        metadata = extractor.extract(text)
        assert metadata.document_type == "checklist"

    def test_detect_document_type_runbook(self, extractor):
        text = "Incident Runbook\nOn-call procedure for handling outages"
        metadata = extractor.extract(text)
        assert metadata.document_type == "runbook"

    def test_complexity_estimation(self, extractor):
        short_text = "Brief note."
        long_text = "Step 1: Do this. " * 100

        short_meta = extractor.extract(short_text)
        long_meta = extractor.extract(long_text)

        assert short_meta.estimated_complexity == "low"
        assert long_meta.estimated_complexity in ["medium", "high"]


class TestEntityExtractor:
    @pytest.fixture
    def extractor(self):
        return EntityExtractor()

    def test_extract_email(self, extractor):
        text = "Contact support@example.com for help."
        entities = extractor.extract(text)
        emails = [e for e in entities if e.entity_type == "email"]
        assert len(emails) == 1
        assert emails[0].name == "support@example.com"

    def test_extract_url(self, extractor):
        text = "Visit https://docs.example.com for documentation."
        entities = extractor.extract(text)
        urls = [e for e in entities if e.entity_type == "url"]
        assert len(urls) == 1

    def test_extract_metrics(self, extractor):
        text = "This process takes 2 hours and costs $500."
        entities = extractor.extract(text)
        metrics = [e for e in entities if e.entity_type == "metric"]
        assert len(metrics) >= 1

    def test_extract_relationships(self, extractor):
        text = "John Smith reports to Jane Doe. Alice manages Bob."
        relationships = extractor.extract_relationships(text)
        assert len(relationships) >= 1


class TestMockEmbedder:
    @pytest.fixture
    def embedder(self):
        return MockEmbedder(dimension=1536)

    @pytest.mark.asyncio
    async def test_embed_returns_correct_dimension(self, embedder):
        embedding = await embedder.embed("test text")
        assert len(embedding) == 1536
        assert all(isinstance(v, float) for v in embedding)

    @pytest.mark.asyncio
    async def test_embed_is_deterministic(self, embedder):
        e1 = await embedder.embed("same text")
        e2 = await embedder.embed("same text")
        assert e1 == e2

    @pytest.mark.asyncio
    async def test_different_texts_different_embeddings(self, embedder):
        e1 = await embedder.embed("text one")
        e2 = await embedder.embed("text two")
        assert e1 != e2

    @pytest.mark.asyncio
    async def test_embed_batch(self, embedder):
        texts = ["one", "two", "three"]
        embeddings = await embedder.embed_batch(texts)
        assert len(embeddings) == 3
        assert all(len(e) == 1536 for e in embeddings)


class TestCivilizationEmbedder:
    @pytest.fixture
    def embedder(self):
        return CivilizationEmbedder(cache_enabled=True)

    @pytest.mark.asyncio
    async def test_caching(self, embedder):
        text = "cache test"
        e1 = await embedder.embed(text)
        e2 = await embedder.embed(text)
        assert e1 == e2

    @pytest.mark.asyncio
    async def test_clear_cache(self, embedder):
        await embedder.embed("text")
        embedder.clear_cache()
        assert len(embedder._cache) == 0

    def test_create_factory(self):
        embedder = CivilizationEmbedder.create("mock", dimension=512)
        assert embedder.dimension == 512
