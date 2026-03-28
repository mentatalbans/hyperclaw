-- HyperMemory Migration 001 — Core schema
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- HyperState persistence
CREATE TABLE IF NOT EXISTS hyperstates (
    state_id        UUID PRIMARY KEY,
    domain          TEXT NOT NULL,
    data            JSONB NOT NULL,
    state_version   INTEGER NOT NULL DEFAULT 1,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hyperstate_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state_id        UUID REFERENCES hyperstates(state_id),
    state_version   INTEGER NOT NULL,
    snapshot        JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Knowledge graph nodes
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label       TEXT NOT NULL,
    node_type   TEXT NOT NULL CHECK (node_type IN ('action','outcome','condition','agent','discovery','skill')),
    domain      TEXT NOT NULL CHECK (domain IN ('personal','business','scientific','creative','recursive')),
    embedding   VECTOR(1536),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS knowledge_nodes_embedding_idx
    ON knowledge_nodes USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS knowledge_nodes_domain_idx ON knowledge_nodes(domain);
CREATE INDEX IF NOT EXISTS knowledge_nodes_type_idx ON knowledge_nodes(node_type);

-- Causal edges
CREATE TABLE IF NOT EXISTS causal_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cause_id    UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    effect_id   UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    method_id   UUID,
    confidence  FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    domain      TEXT NOT NULL,
    context     JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS causal_edges_cause_idx ON causal_edges(cause_id);
CREATE INDEX IF NOT EXISTS causal_edges_effect_idx ON causal_edges(effect_id);

-- Impact records
CREATE TABLE IF NOT EXISTS impact_records (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain           TEXT NOT NULL,
    task             TEXT NOT NULL,
    baseline_metric  TEXT NOT NULL,
    baseline_value   FLOAT NOT NULL,
    outcome_metric   TEXT NOT NULL,
    outcome_value    FLOAT NOT NULL,
    delta            FLOAT NOT NULL,
    delta_pct        FLOAT NOT NULL,
    certified_at     TIMESTAMPTZ DEFAULT now(),
    method_id        UUID,
    notes            TEXT
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT NOT NULL,
    agent_id        TEXT,
    model_used      TEXT,
    action          TEXT NOT NULL,
    target          TEXT,
    allowed         BOOLEAN NOT NULL,
    policy_applied  TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- SwarmMessage log
CREATE TABLE IF NOT EXISTS swarm_messages (
    message_id   UUID PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    model_used   TEXT NOT NULL,
    task_type    TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms   FLOAT,
    cost_usd     FLOAT,
    certified    BOOLEAN DEFAULT false,
    state_id     UUID,
    created_at   TIMESTAMPTZ DEFAULT now()
);
