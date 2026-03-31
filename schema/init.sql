-- HyperClaw Database Schema
-- Run this on your PostgreSQL/Supabase database

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- KNOWLEDGE GRAPH TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    label TEXT NOT NULL,
    node_type VARCHAR(50) NOT NULL,  -- 'fact', 'decision', 'episode', 'instinct'
    domain VARCHAR(50),
    embedding vector(1536),  -- For semantic search
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kg_edges (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation VARCHAR(100) NOT NULL,  -- 'causes', 'relates_to', 'precedes', etc.
    confidence FLOAT DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    domain VARCHAR(50),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for knowledge graph
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_domain ON knowledge_nodes(domain);
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_type ON knowledge_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_embedding ON knowledge_nodes USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);

-- ============================================================================
-- STATE MANAGEMENT TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS hyperstate (
    state_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain VARCHAR(50) NOT NULL,
    task_goal TEXT,
    task_type VARCHAR(50),
    priority INTEGER DEFAULT 5,
    status VARCHAR(20) DEFAULT 'active',  -- 'active', 'completed', 'failed'
    state_data JSONB DEFAULT '{}',
    agent_scores JSONB DEFAULT '{}',
    model_scores JSONB DEFAULT '{}',
    state_version INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS state_mutations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    state_id UUID NOT NULL REFERENCES hyperstate(state_id) ON DELETE CASCADE,
    mutation_type VARCHAR(50) NOT NULL,
    before_state JSONB,
    after_state JSONB,
    agent_id VARCHAR(100),
    causal_node_id UUID REFERENCES knowledge_nodes(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hyperstate_domain ON hyperstate(domain);
CREATE INDEX IF NOT EXISTS idx_hyperstate_status ON hyperstate(status);
CREATE INDEX IF NOT EXISTS idx_state_mutations_state ON state_mutations(state_id);

-- ============================================================================
-- TASK MANAGEMENT TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS tasks (
    task_id VARCHAR(12) PRIMARY KEY,
    goal TEXT NOT NULL,
    domain VARCHAR(50) NOT NULL,
    task_type VARCHAR(50) NOT NULL,
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    assigned_to VARCHAR(100),
    status VARCHAR(20) DEFAULT 'pending',  -- 'pending', 'running', 'done', 'failed'
    result TEXT,
    error TEXT,
    parent_task_id VARCHAR(12) REFERENCES tasks(task_id),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_domain ON tasks(domain);

-- ============================================================================
-- MEMORY TABLES (Episodic + Semantic)
-- ============================================================================

CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    memory_type VARCHAR(20) NOT NULL,  -- 'episode', 'semantic', 'instinct', 'dream'
    content TEXT NOT NULL,
    summary TEXT,
    domain VARCHAR(50),
    importance FLOAT DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    embedding vector(1536),
    source VARCHAR(100),  -- 'conversation', 'consolidation', 'user_explicit'
    metadata JSONB DEFAULT '{}',
    is_core BOOLEAN DEFAULT FALSE,  -- Core memories are never pruned
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE  -- NULL means never expires
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_memories_core ON memories(is_core) WHERE is_core = TRUE;

-- ============================================================================
-- CONVERSATION HISTORY
-- ============================================================================

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id VARCHAR(100) NOT NULL,
    channel VARCHAR(50) NOT NULL,  -- 'telegram', 'api', 'tui', 'web'
    user_id VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_message_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,  -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,
    agent_id VARCHAR(100),
    model_used VARCHAR(100),
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conversations_channel ON conversations(channel);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);

-- ============================================================================
-- AUDIT & SECURITY TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    agent_id VARCHAR(100),
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    details JSONB DEFAULT '{}',
    result VARCHAR(20),  -- 'allowed', 'blocked', 'error'
    error_message TEXT,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_result ON audit_log(result);

-- ============================================================================
-- INTEGRATION STATE
-- ============================================================================

CREATE TABLE IF NOT EXISTS integration_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    integration_name VARCHAR(100) NOT NULL UNIQUE,
    status VARCHAR(20) DEFAULT 'disconnected',  -- 'connected', 'disconnected', 'error'
    last_sync TIMESTAMP WITH TIME ZONE,
    credentials_hash VARCHAR(64),  -- SHA-256 hash to detect changes
    config JSONB DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    integration_name VARCHAR(100) NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_type VARCHAR(50) DEFAULT 'Bearer',
    expires_at TIMESTAMP WITH TIME ZONE,
    scopes TEXT[],
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oauth_integration ON oauth_tokens(integration_name);

-- ============================================================================
-- SCHEDULER STATE
-- ============================================================================

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_name VARCHAR(100) NOT NULL UNIQUE,
    cron_expression VARCHAR(100),
    next_run TIMESTAMP WITH TIME ZONE,
    last_run TIMESTAMP WITH TIME ZONE,
    last_result VARCHAR(20),  -- 'success', 'failed', 'skipped'
    run_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply to tables that need auto-update
CREATE TRIGGER update_knowledge_nodes_updated_at
    BEFORE UPDATE ON knowledge_nodes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_hyperstate_updated_at
    BEFORE UPDATE ON hyperstate
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_integration_state_updated_at
    BEFORE UPDATE ON integration_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Function for semantic search on memories
CREATE OR REPLACE FUNCTION search_memories(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 10,
    filter_domain VARCHAR(50) DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    summary TEXT,
    domain VARCHAR(50),
    importance FLOAT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.content,
        m.summary,
        m.domain,
        m.importance,
        1 - (m.embedding <=> query_embedding) as similarity
    FROM memories m
    WHERE
        m.embedding IS NOT NULL
        AND (filter_domain IS NULL OR m.domain = filter_domain)
        AND 1 - (m.embedding <=> query_embedding) > match_threshold
    ORDER BY m.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Function for semantic search on knowledge nodes
CREATE OR REPLACE FUNCTION search_knowledge(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 10
)
RETURNS TABLE (
    id UUID,
    label TEXT,
    node_type VARCHAR(50),
    domain VARCHAR(50),
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        k.id,
        k.label,
        k.node_type,
        k.domain,
        1 - (k.embedding <=> query_embedding) as similarity
    FROM knowledge_nodes k
    WHERE
        k.embedding IS NOT NULL
        AND 1 - (k.embedding <=> query_embedding) > match_threshold
    ORDER BY k.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- ROW LEVEL SECURITY (Optional - enable if needed)
-- ============================================================================

-- Enable RLS on sensitive tables (uncomment if using Supabase with auth)
-- ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE oauth_tokens ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- INITIAL DATA
-- ============================================================================

-- Insert default integration states
INSERT INTO integration_state (integration_name, status, config) VALUES
    ('telegram', 'disconnected', '{"type": "bot"}'),
    ('gmail', 'disconnected', '{"type": "oauth2"}'),
    ('google_calendar', 'disconnected', '{"type": "oauth2"}'),
    ('imessage', 'disconnected', '{"type": "local"}'),
    ('slack', 'disconnected', '{"type": "oauth2"}'),
    ('whatsapp', 'disconnected', '{"type": "twilio"}')
ON CONFLICT (integration_name) DO NOTHING;

-- Insert default scheduled jobs
INSERT INTO scheduled_jobs (job_name, cron_expression, enabled, config) VALUES
    ('heartbeat', '*/10 * * * *', true, '{"description": "System health check"}'),
    ('memory_consolidation', '30 2 * * *', true, '{"description": "Nightly memory consolidation"}'),
    ('morning_brief', '0 8 * * *', true, '{"description": "Daily morning briefing"}'),
    ('email_check', '*/5 * * * *', true, '{"description": "Check for new emails"}'),
    ('calendar_sync', '*/15 * * * *', true, '{"description": "Sync calendar events"}')
ON CONFLICT (job_name) DO NOTHING;

-- Done!
SELECT 'HyperClaw database schema initialized successfully' as status;
