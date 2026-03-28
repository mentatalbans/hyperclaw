# HyperClaw — Supabase Setup Guide
## ⏱ ~5 minutes | One-time setup

HyperClaw uses Supabase for its vector memory store (HyperMemory).
This guide gets you from zero to a working Supabase project with pgvector enabled.

---

## Step 1: Create a Supabase Project

1. Go to **https://app.supabase.com**
2. Click **"New Project"**
3. Choose your organization (or create one)
4. Set:
   - **Project name:** `hyperclaw-prod`
   - **Database password:** *(save this — you'll need it)*
   - **Region:** `US East (N. Virginia)` — or closest to your server
5. Click **"Create new project"** — takes ~2 minutes

---

## Step 2: Enable pgvector Extension

1. In your project dashboard → **Database** → **Extensions**
2. Search for `vector`
3. Click **Enable** on `pgvector`

---

## Step 3: Run the Schema Setup

1. Go to **SQL Editor** in your project
2. Paste and run:

```sql
-- Enable vector extension (if not done via UI)
CREATE EXTENSION IF NOT EXISTS vector;

-- HyperMemory table
CREATE TABLE IF NOT EXISTS memories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  namespace TEXT NOT NULL DEFAULT 'default',
  content TEXT NOT NULL,
  embedding vector(1536),
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast vector similarity search
CREATE INDEX IF NOT EXISTS memories_embedding_idx
  ON memories USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- Index for namespace filtering
CREATE INDEX IF NOT EXISTS memories_namespace_idx ON memories(namespace);
```

---

## Step 4: Grab Your Credentials

1. Go to **Settings** → **API** in your project
2. Copy these values:

| What | Where | Goes into |
|------|-------|-----------|
| **Project URL** | `https://xxxx.supabase.co` | `SUPABASE_URL` |
| **anon / public key** | Under "Project API keys" | `SUPABASE_KEY` |
| **Database URL** | Settings → Database → Connection string → URI | `DATABASE_URL` |

**For DATABASE_URL:** Use the **Transaction Pooler** URL for production:
```
postgresql://postgres.[ref]:[password]@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

---

## Step 5: Update Your .env File

```bash
cd /Users/mentat/.openclaw/workspace/hyperclaw
cp .env.example .env
nano .env  # or open in your editor
```

Fill in:
```
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
DATABASE_URL=postgresql://postgres.xxxx:yourpassword@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

---

## Step 6: Verify Connection

```bash
cd /Users/mentat/.openclaw/workspace/hyperclaw
source .venv/bin/activate
python3 -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()
async def test():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    result = await conn.fetchval('SELECT COUNT(*) FROM memories')
    print(f'HyperMemory connected — {result} memories stored')
    await conn.close()
asyncio.run(test())
"
```

---

## ✅ Done

Once `.env` is set, restart the server:
```bash
python3 server.py
```

The `/health` endpoint will show `"database": "connected"` and HyperMemory will activate.

---

*Need help? The SOLOMON dashboard memory stats panel will show live status once connected.*
