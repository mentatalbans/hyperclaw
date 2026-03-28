-- Civilization Knowledge Layer Migration 002
-- Organizational knowledge graph schema

-- Main civilization nodes table
CREATE TABLE IF NOT EXISTS civilization_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL,
    node_type       TEXT NOT NULL CHECK (node_type IN (
        'sop', 'job_description', 'checklist', 'runbook', 'workflow',
        'org_chart', 'role', 'person', 'client_profile', 'personal_routine',
        'policy', 'knowledge_article'
    )),
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
        'draft', 'active', 'under_review', 'deprecated', 'archived'
    )),
    version         TEXT NOT NULL DEFAULT '1.0.0',
    owner_id        TEXT,
    tags            TEXT[] DEFAULT '{}',
    source          TEXT,
    source_url      TEXT,
    content         JSONB NOT NULL,
    embedding       VECTOR(1536),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Indexes for civilization_nodes
CREATE INDEX IF NOT EXISTS civilization_nodes_org_idx
    ON civilization_nodes(org_id);

CREATE INDEX IF NOT EXISTS civilization_nodes_type_idx
    ON civilization_nodes(org_id, node_type);

CREATE INDEX IF NOT EXISTS civilization_nodes_status_idx
    ON civilization_nodes(org_id, status);

CREATE INDEX IF NOT EXISTS civilization_nodes_owner_idx
    ON civilization_nodes(org_id, owner_id) WHERE owner_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS civilization_nodes_tags_idx
    ON civilization_nodes USING GIN(tags);

CREATE INDEX IF NOT EXISTS civilization_nodes_embedding_idx
    ON civilization_nodes USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS civilization_nodes_updated_idx
    ON civilization_nodes(org_id, updated_at DESC);

-- Full-text search index
CREATE INDEX IF NOT EXISTS civilization_nodes_search_idx
    ON civilization_nodes USING GIN(to_tsvector('english', title || ' ' || COALESCE(content->>'purpose', '') || ' ' || COALESCE(content->>'summary', '')));

-- Version history for civilization nodes
CREATE TABLE IF NOT EXISTS civilization_versions (
    version_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         UUID NOT NULL REFERENCES civilization_nodes(id) ON DELETE CASCADE,
    version_number  TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    snapshot        JSONB NOT NULL,
    change_summary  TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS civilization_versions_node_idx
    ON civilization_versions(node_id, created_at DESC);

-- Knowledge links between nodes
CREATE TABLE IF NOT EXISTS civilization_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL,
    source_id       UUID NOT NULL REFERENCES civilization_nodes(id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES civilization_nodes(id) ON DELETE CASCADE,
    link_type       TEXT NOT NULL CHECK (link_type IN (
        'parent_of', 'child_of', 'precedes', 'follows', 'triggers', 'triggered_by',
        'owns', 'owned_by', 'responsible_for', 'assigned_to',
        'references', 'referenced_by', 'related_to',
        'requires_role', 'required_by_role', 'uses_tool',
        'version_of', 'supersedes', 'superseded_by'
    )),
    strength        FLOAT DEFAULT 1.0 CHECK (strength >= 0.0 AND strength <= 1.0),
    auto_detected   BOOLEAN DEFAULT false,
    description     TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(source_id, target_id, link_type)
);

CREATE INDEX IF NOT EXISTS civilization_links_source_idx
    ON civilization_links(source_id);

CREATE INDEX IF NOT EXISTS civilization_links_target_idx
    ON civilization_links(target_id);

CREATE INDEX IF NOT EXISTS civilization_links_type_idx
    ON civilization_links(org_id, link_type);

-- Chunks for retrieval (procedural chunking)
CREATE TABLE IF NOT EXISTS civilization_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         UUID NOT NULL REFERENCES civilization_nodes(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_type      TEXT NOT NULL CHECK (chunk_type IN ('sop_step', 'checklist_item', 'runbook_step', 'generic')),
    content         TEXT NOT NULL,
    embedding       VECTOR(1536),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(node_id, chunk_index, chunk_type)
);

CREATE INDEX IF NOT EXISTS civilization_chunks_node_idx
    ON civilization_chunks(node_id);

CREATE INDEX IF NOT EXISTS civilization_chunks_embedding_idx
    ON civilization_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Interview sessions
CREATE TABLE IF NOT EXISTS civilization_interviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    subject         TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'not_started' CHECK (state IN (
        'not_started', 'in_progress', 'paused', 'completed', 'abandoned'
    )),
    interviewee_id  TEXT,
    responses       JSONB DEFAULT '[]',
    current_index   INTEGER DEFAULT 0,
    total_questions INTEGER DEFAULT 0,
    compiled_node_id UUID REFERENCES civilization_nodes(id),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS civilization_interviews_org_idx
    ON civilization_interviews(org_id, state);

-- Gap analysis results
CREATE TABLE IF NOT EXISTS civilization_gap_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL,
    coverage_score  FLOAT,
    health_score    FLOAT,
    gaps            JSONB DEFAULT '[]',
    recommendations JSONB DEFAULT '[]',
    generated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS civilization_gap_reports_org_idx
    ON civilization_gap_reports(org_id, generated_at DESC);

-- Sync state for external sources
CREATE TABLE IF NOT EXISTS civilization_sync_state (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL,
    source_type     TEXT NOT NULL CHECK (source_type IN ('notion', 'confluence', 'gdrive', 'sharepoint')),
    source_id       TEXT NOT NULL,
    last_sync       TIMESTAMPTZ,
    sync_cursor     TEXT,
    metadata        JSONB DEFAULT '{}',
    UNIQUE(org_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS civilization_sync_state_org_idx
    ON civilization_sync_state(org_id, source_type);

-- Helper function: update updated_at on modification
CREATE OR REPLACE FUNCTION update_civilization_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for auto-updating timestamp
DROP TRIGGER IF EXISTS civilization_nodes_updated ON civilization_nodes;
CREATE TRIGGER civilization_nodes_updated
    BEFORE UPDATE ON civilization_nodes
    FOR EACH ROW
    EXECUTE FUNCTION update_civilization_timestamp();
