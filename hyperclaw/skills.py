"""
HyperClaw Skills Engine
=======================
Manages Assistant's and the swarm's skill library.
- Skills are atomic, reusable capabilities agents can invoke
- GENESIS (scout) discovers new skills and proposes them
- Skills are versioned, tagged, and safety-audited before activation
- All new skills go through a 3-step pipeline: Discover → Audit → Activate

the user's directive: Scouts should always be looking for new skills
that improve Assistant's overall capability.
"""

from __future__ import annotations

import json
import logging
import hashlib
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("hyperclaw.skills")

HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", str(Path.home() / ".hyperclaw")))
SKILLS_DIR = HYPERCLAW_ROOT / "config" / "skills"
SKILLS_REGISTRY = SKILLS_DIR / "registry.json"
SKILLS_PROPOSALS = SKILLS_DIR / "proposals.json"

SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# ── Skill Schema ──────────────────────────────────────────────────────────────

class Skill:
    """A single atomic capability."""

    def __init__(self, data: dict):
        self.id = data["id"]
        self.name = data["name"]
        self.description = data["description"]
        self.category = data.get("category", "general")
        self.tags = data.get("tags", [])
        self.agents = data.get("agents", [])          # Which agents can use this
        self.implementation = data.get("implementation", "")
        self.status = data.get("status", "active")    # active | proposed | deprecated
        self.safety_score = data.get("safety_score", 1.0)  # 0.0-1.0
        self.added_at = data.get("added_at", datetime.now().isoformat())
        self.source = data.get("source", "manual")   # manual | genesis | openclaw
        self.version = data.get("version", "1.0.0")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "agents": self.agents,
            "implementation": self.implementation,
            "status": self.status,
            "safety_score": self.safety_score,
            "added_at": self.added_at,
            "source": self.source,
            "version": self.version,
        }

    def __repr__(self):
        return f"<Skill {self.id}: {self.name} [{self.category}] {self.status}>"


# ── Skills Registry ───────────────────────────────────────────────────────────

class SkillsRegistry:
    """
    Central registry of all Assistant and swarm skills.
    Persists to JSON. Thread-safe via asyncio lock.
    """

    # Core skills — pre-loaded, always active
    CORE_SKILLS = [
        {
            "id": "skill_email_read",
            "name": "Email Read",
            "description": "Read and parse Gmail inbox messages",
            "category": "communications",
            "tags": ["gmail", "email", "inbox", "read"],
            "agents": ["gil", "comms_agent", "briefing_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_email_send",
            "name": "Email Send",
            "description": "Compose and send emails via Gmail with CC policy enforcement",
            "category": "communications",
            "tags": ["gmail", "email", "send", "draft"],
            "agents": ["gil", "comms_agent", "seldon_sales"],
            "status": "active",
            "safety_score": 0.9,
            "source": "core",
        },
        {
            "id": "skill_telegram_send",
            "name": "Telegram Send",
            "description": "Send messages to the user via Telegram bot",
            "category": "communications",
            "tags": ["telegram", "message", "notify"],
            "agents": ["gil", "solomon", "telegram_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_imessage",
            "name": "iMessage",
            "description": "Send and read iMessages via native macOS AppleScript",
            "category": "communications",
            "tags": ["imessage", "sms", "apple"],
            "agents": ["gil", "comms_agent"],
            "status": "active",
            "safety_score": 0.95,
            "source": "core",
        },
        {
            "id": "skill_calendar_read",
            "name": "Calendar Read",
            "description": "Read Google Calendar events for the user",
            "category": "scheduling",
            "tags": ["calendar", "events", "schedule"],
            "agents": ["gil", "personal_assistant"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_calendar_create",
            "name": "Calendar Create",
            "description": "Create Google Calendar events with attendees",
            "category": "scheduling",
            "tags": ["calendar", "event", "create", "invite"],
            "agents": ["gil", "personal_assistant"],
            "status": "active",
            "safety_score": 0.95,
            "source": "core",
        },
        {
            "id": "skill_bash_execute",
            "name": "Bash Execute",
            "description": "Execute shell commands on the local macOS system",
            "category": "system",
            "tags": ["bash", "shell", "execute", "system"],
            "agents": ["gil", "code_specialist", "infra_agent", "security_agent"],
            "status": "active",
            "safety_score": 0.85,
            "source": "core",
        },
        {
            "id": "skill_file_read",
            "name": "File Read",
            "description": "Read any file from the local filesystem",
            "category": "system",
            "tags": ["file", "read", "filesystem"],
            "agents": ["*"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_file_write",
            "name": "File Write",
            "description": "Write and create files on the local filesystem",
            "category": "system",
            "tags": ["file", "write", "create", "filesystem"],
            "agents": ["*"],
            "status": "active",
            "safety_score": 0.95,
            "source": "core",
        },
        {
            "id": "skill_supabase_query",
            "name": "Supabase Query",
            "description": "Query HyperClaw Supabase database tables",
            "category": "data",
            "tags": ["supabase", "database", "query", "sql"],
            "agents": ["gil", "db_agent", "memory_agent", "ledger"],
            "status": "active",
            "safety_score": 0.95,
            "source": "core",
        },
        {
            "id": "skill_memory_store",
            "name": "Memory Store",
            "description": "Store episodic memories in Supabase for long-term persistence",
            "category": "memory",
            "tags": ["memory", "engram", "store", "persist"],
            "agents": ["*"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_web_search",
            "name": "Web Research",
            "description": "Search the web and retrieve live information via bash/curl",
            "category": "research",
            "tags": ["web", "search", "research", "internet"],
            "agents": ["gil", "intel_agent", "nexus_strategy", "core_reasoner"],
            "status": "active",
            "safety_score": 0.9,
            "source": "core",
        },
        {
            "id": "skill_swarm_dispatch",
            "name": "Swarm Dispatch",
            "description": "Route tasks to specialist agents in the HyperSwarm",
            "category": "orchestration",
            "tags": ["swarm", "dispatch", "route", "delegate"],
            "agents": ["gil", "solomon"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_trading_monitor",
            "name": "Trading Monitor",
            "description": "Monitor SATOSHI trading positions and P&L on Hyperliquid",
            "category": "trading",
            "tags": ["satoshi", "trading", "hyperliquid", "pnl"],
            "agents": ["satoshi_agent", "gil"],
            "status": "active",
            "safety_score": 0.9,
            "source": "core",
        },
        {
            "id": "skill_elevenlabs_tts",
            "name": "ElevenLabs TTS",
            "description": "Convert text to speech using ElevenLabs George voice",
            "category": "voice",
            "tags": ["tts", "voice", "audio", "elevenlabs", "george"],
            "agents": ["voice_agent", "gil"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_runway_video",
            "name": "Runway Video Generation",
            "description": "Generate video content via Runway ML API (350 credits)",
            "category": "creative",
            "tags": ["runway", "video", "generate", "ai", "creative"],
            "agents": ["video_agent", "creative_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_news_feed",
            "name": "News Intelligence Feed",
            "description": "Parse 60+ global news sources for intelligence relevant to the organization",
            "category": "intelligence",
            "tags": ["news", "feed", "intelligence", "global"],
            "agents": ["intel_agent", "briefing_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_document_analysis",
            "name": "Document Analysis",
            "description": "Read, parse, and extract insights from PDFs, docs, and text files",
            "category": "analysis",
            "tags": ["document", "pdf", "parse", "extract", "analysis"],
            "agents": ["*"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_code_generation",
            "name": "Code Generation",
            "description": "Write, debug, and deploy production-quality code across languages",
            "category": "technology",
            "tags": ["code", "python", "javascript", "debug", "build"],
            "agents": ["code_specialist", "infra_agent", "gil"],
            "status": "active",
            "safety_score": 0.9,
            "source": "core",
        },
        {
            "id": "skill_financial_modeling",
            "name": "Financial Modeling",
            "description": "Build P&L models, projections, and financial analysis",
            "category": "finance",
            "tags": ["finance", "model", "projections", "analysis", "pnl"],
            "agents": ["finance_agent", "core_reasoner"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_contract_review",
            "name": "Contract Review",
            "description": "Review contracts, term sheets, and legal documents for risk flags",
            "category": "legal",
            "tags": ["contract", "legal", "review", "terms"],
            "agents": ["legal_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_talent_intelligence",
            "name": "Talent Intelligence",
            "description": "Research athletes, entertainers, and talent for talent management roster",
            "category": "talent",
            "tags": ["talent", "athlete", "research", "roster"],
            "agents": ["talent_agent", "talent_ops_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_brand_strategy",
            "name": "Brand Strategy",
            "description": "Develop brand positioning, narrative, and marketing strategy",
            "category": "marketing",
            "tags": ["brand", "strategy", "marketing", "positioning"],
            "agents": ["creative_agent", "nexus_strategy", "pr_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_pitch_deck",
            "name": "Pitch Deck Creation",
            "description": "Create investor pitch decks and presentations for the organization",
            "category": "business",
            "tags": ["pitch", "deck", "investor", "presentation"],
            "agents": ["copy_agent", "nexus_strategy", "finance_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_geopolitical_analysis",
            "name": "Geopolitical Analysis",
            "description": "Analyze geopolitical trends relevant to the organization global expansion",
            "category": "intelligence",
            "tags": ["geopolitics", "global", "analysis", "trends"],
            "agents": ["geo_agent", "intel_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_crypto_analysis",
            "name": "Crypto & Web3 Analysis",
            "description": "Analyze crypto markets, DeFi protocols, and blockchain opportunities",
            "category": "crypto",
            "tags": ["crypto", "defi", "blockchain", "web3", "analysis"],
            "agents": ["crypto_agent", "satoshi_agent"],
            "status": "active",
            "safety_score": 0.85,
            "source": "core",
        },
        {
            "id": "skill_space_research",
            "name": "Space Technology Research",
            "description": "Research satellite, CubeSat, and space tech opportunities for Hyper Space vertical",
            "category": "scientific",
            "tags": ["space", "satellite", "cubesat", "guyana", "research"],
            "agents": ["space_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_health_research",
            "name": "Healthcare Research",
            "description": "Research drug discovery, concierge medicine, and hospital AI for Hyper Health",
            "category": "scientific",
            "tags": ["health", "medical", "drug", "research", "ai"],
            "agents": ["health_research_agent", "health_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_optimization",
            "name": "System Optimization",
            "description": "Identify and resolve performance bottlenecks across HyperClaw infrastructure",
            "category": "technology",
            "tags": ["optimize", "performance", "system", "audit"],
            "agents": ["optimization_agent", "infra_agent"],
            "status": "active",
            "safety_score": 0.95,
            "source": "core",
        },
        {
            "id": "skill_security_audit",
            "name": "Security Audit",
            "description": "Scan for vulnerabilities, RLS gaps, and security threats across HyperClaw",
            "category": "security",
            "tags": ["security", "audit", "vulnerability", "rls", "threat"],
            "agents": ["security_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
        {
            "id": "skill_prediction_market",
            "name": "Prediction Market Analysis",
            "description": "Query and analyze Polymarket prediction markets for strategic intelligence",
            "category": "intelligence",
            "tags": ["polymarket", "prediction", "probability", "forecast"],
            "agents": ["prediction_agent"],
            "status": "active",
            "safety_score": 1.0,
            "source": "core",
        },
    ]

    def __init__(self):
        self._lock = asyncio.Lock()
        self._skills: dict[str, Skill] = {}
        self._proposals: list[dict] = []
        self._load()

    def _load(self):
        """Load skills from disk, seeding with core skills."""
        # Load core skills first
        for s in self.CORE_SKILLS:
            s.setdefault("added_at", datetime.now().isoformat())
            s.setdefault("version", "1.0.0")
            skill = Skill(s)
            self._skills[skill.id] = skill

        # Load persisted skills
        if SKILLS_REGISTRY.exists():
            try:
                data = json.loads(SKILLS_REGISTRY.read_text())
                for s in data.get("skills", []):
                    skill = Skill(s)
                    self._skills[skill.id] = skill
            except Exception as e:
                logger.error(f"Skills registry load error: {e}")

        # Load proposals
        if SKILLS_PROPOSALS.exists():
            try:
                data = json.loads(SKILLS_PROPOSALS.read_text())
                self._proposals = data.get("proposals", [])
            except Exception:
                self._proposals = []

        logger.info(f"Skills loaded: {len(self._skills)} active, {len(self._proposals)} proposed")

    def _save(self):
        """Persist skills to disk."""
        try:
            data = {"skills": [s.to_dict() for s in self._skills.values()
                               if s.source != "core"]}
            SKILLS_REGISTRY.write_text(json.dumps(data, indent=2))

            prop_data = {"proposals": self._proposals}
            SKILLS_PROPOSALS.write_text(json.dumps(prop_data, indent=2))
        except Exception as e:
            logger.error(f"Skills save error: {e}")

    def get(self, skill_id: str) -> Optional[Skill]:
        return self._skills.get(skill_id)

    def list_all(self, status: str = None, category: str = None) -> list[Skill]:
        skills = list(self._skills.values())
        if status:
            skills = [s for s in skills if s.status == status]
        if category:
            skills = [s for s in skills if s.category == category]
        return skills

    def list_for_agent(self, agent_id: str) -> list[Skill]:
        """Return all active skills available to a given agent."""
        return [
            s for s in self._skills.values()
            if s.status == "active" and (
                "*" in s.agents or agent_id in s.agents
            )
        ]

    async def add_skill(self, skill_data: dict, source: str = "genesis") -> Skill:
        """Add a new skill (post safety audit)."""
        async with self._lock:
            skill_data["source"] = source
            skill_data["added_at"] = datetime.now().isoformat()
            skill_data.setdefault("version", "1.0.0")
            skill_data.setdefault("status", "active")
            skill = Skill(skill_data)
            self._skills[skill.id] = skill
            self._save()
            logger.info(f"Skill added: {skill.id} ({skill.name}) from {source}")
            return skill

    async def propose_skill(self, proposal: dict) -> str:
        """GENESIS submits a skill proposal for audit."""
        async with self._lock:
            proposal["proposed_at"] = datetime.now().isoformat()
            proposal["status"] = "proposed"
            proposal_id = hashlib.md5(
                (proposal.get("name", "") + proposal.get("proposed_at", "")).encode()
            ).hexdigest()[:8]
            proposal["proposal_id"] = proposal_id
            self._proposals.append(proposal)
            self._save()
            logger.info(f"Skill proposed: {proposal.get('name')} (ID: {proposal_id})")
            return proposal_id

    def get_proposals(self) -> list[dict]:
        return self._proposals

    def status_summary(self) -> dict:
        by_cat = {}
        for s in self._skills.values():
            cat = s.category
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "total_skills": len(self._skills),
            "active": len([s for s in self._skills.values() if s.status == "active"]),
            "proposed": len(self._proposals),
            "by_category": by_cat,
        }


# ── GENESIS — Capability Scout ─────────────────────────────────────────────────

class GenesisScout:
    """
    GENESIS capability scout.
    Continuously monitors the swarm for capability gaps and proposes new skills.
    Runs as a background task — reports findings to Assistant via the scheduler.

    the user's directive: "Always have your scouts, agents look for new skills
    that will improve your overall capability."
    """

    DISCOVERY_PROMPT = """You are GENESIS — the HyperClaw self-expansion engine.

Your mission: Identify capability gaps in the current AI agent swarm and propose NEW SKILLS
that would meaningfully improve Assistant and the team's ability to serve the user.

Current swarm has {agent_count} agents across these domains: {domains}

Current skill categories: {categories}

Consider:
1. What tasks come up repeatedly that could be automated?
2. What external APIs or data sources would improve intelligence quality?
3. What workflows are manual that could become agent skills?
4. What capabilities do world-class executive AI systems have that we lack?
5. What would specifically help the organization (hospitality AI, $30M ARR target) and talent management?

Generate exactly 5 NEW SKILL PROPOSALS in this JSON format:
{{
  "proposals": [
    {{
      "id": "skill_unique_id",
      "name": "Skill Name",
      "description": "What it does in 1-2 sentences",
      "category": "category",
      "tags": ["tag1", "tag2"],
      "agents": ["agent_id_1", "agent_id_2"],
      "implementation": "How it would be implemented (tool, API, or capability)",
      "why_valuable": "Why this improves Assistant's capability specifically",
      "safety_score": 0.95
    }}
  ]
}}

Focus on skills that are:
- SAFE (no data exfiltration, no unauthorized external actions)
- PRACTICAL (can be implemented within HyperClaw's infrastructure)
- HIGH-VALUE (meaningfully improves the user's AI executive system)

Current context: {context}
"""

    def __init__(self, registry: SkillsRegistry):
        self.registry = registry
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self._last_scan = None

    async def scan(self, swarm_status: dict = None) -> list[dict]:
        """
        Run a capability scan. Propose new skills.
        Returns list of proposals submitted.
        """
        logger.info("GENESIS: Running capability scan...")

        # Build context
        agent_count = swarm_status.get("total_agents", 56) if swarm_status else 56
        domains = list(swarm_status.get("domains", {}).keys()) if swarm_status else []
        current_skills = self.registry.list_all(status="active")
        categories = list(set(s.category for s in current_skills))

        # Build context summary
        context = f"""
- the organization: April 2026 GTM, $30M Year 1 ARR target, 8 AI products, 210 LOIs
- talent management: 10 signed athletes/entertainers, post-revenue, seeking $2M seed
- SATOSHI: Live trading on Hyperliquid, ~$338 balance, 5 positions
- Infrastructure: macOS + FastAPI + Supabase + Telegram + Gmail + ElevenLabs + Runway ML
- Current tools: email, calendar, iMessage, bash, file I/O, Supabase, Telegram, ElevenLabs TTS, Runway video
- Gap areas: WhatsApp API, LinkedIn intelligence, Polymarket live data, Guyana space intel, sports analytics
"""
        prompt = self.DISCOVERY_PROMPT.format(
            agent_count=agent_count,
            domains=", ".join(domains) if domains else "executive, business, comms, personal, scientific, technology, creative, talent, recursive",
            categories=", ".join(categories),
            context=context,
        )

        try:
            response = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-haiku-3-5",  # Fast + cheap for discovery
                max_tokens=2000,
                system="You are GENESIS, HyperClaw's self-expansion engine. Output valid JSON only. No markdown, no explanation.",
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            # Clean up any markdown code blocks
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            data = json.loads(raw)
            proposals = data.get("proposals", [])

            submitted = []
            for proposal in proposals:
                # Safety gate: reject low safety scores
                if proposal.get("safety_score", 0) < 0.7:
                    logger.warning(f"GENESIS: Rejected unsafe skill proposal: {proposal.get('name')}")
                    continue

                proposal_id = await self.registry.propose_skill(proposal)
                submitted.append({**proposal, "proposal_id": proposal_id})
                logger.info(f"GENESIS: Proposed skill '{proposal.get('name')}' (ID: {proposal_id})")

            self._last_scan = datetime.now().isoformat()
            return submitted

        except json.JSONDecodeError as e:
            logger.error(f"GENESIS: JSON parse error: {e}")
            return []
        except Exception as e:
            logger.error(f"GENESIS: Scan error: {e}")
            return []

    async def auto_activate_safe_proposals(self) -> list[Skill]:
        """
        Auto-activate proposals with safety_score >= 0.95.
        Lower scores are held for the user's approval.
        """
        proposals = self.registry.get_proposals()
        activated = []

        for proposal in proposals:
            if proposal.get("status") == "proposed" and proposal.get("safety_score", 0) >= 0.95:
                skill = await self.registry.add_skill(proposal, source="genesis")
                proposal["status"] = "activated"
                activated.append(skill)
                logger.info(f"GENESIS: Auto-activated skill: {skill.name}")

        if activated:
            self.registry._save()

        return activated


# ── Singletons ────────────────────────────────────────────────────────────────

_registry: Optional[SkillsRegistry] = None
_genesis: Optional[GenesisScout] = None


def get_skills_registry() -> SkillsRegistry:
    global _registry
    if _registry is None:
        _registry = SkillsRegistry()
    return _registry


def get_genesis_scout() -> GenesisScout:
    global _genesis
    if _genesis is None:
        _genesis = GenesisScout(get_skills_registry())
    return _genesis
