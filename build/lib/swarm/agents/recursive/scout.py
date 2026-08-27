"""SCOUT — Capability Research. Monitors arXiv, GitHub, PubMed for actionable discoveries."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


@dataclass
class Discovery:
    discovery_id: UUID = field(default_factory=uuid.uuid4)
    source_name: str = ""
    title: str = ""
    url: str = ""
    summary: str = ""
    domain: str = "scientific"
    relevance_score: float = 0.0
    actionable: bool = False
    validated: bool = False
    added_to_knowledge_graph: bool = False
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


SOURCES = [
    {"name": "arxiv-cs-ai", "domain": "recursive", "url": "https://arxiv.org/list/cs.AI/recent"},
    {"name": "arxiv-q-bio", "domain": "scientific", "url": "https://arxiv.org/list/q-bio/recent"},
    {"name": "arxiv-astro-ph", "domain": "scientific", "url": "https://arxiv.org/list/astro-ph/recent"},
    {"name": "github-trending-python", "domain": "recursive", "url": "https://github.com/trending/python"},
    {"name": "pubmed-ai-health", "domain": "scientific", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=artificial+intelligence+healthcare"},
]


class ScoutAgent(BaseAgent):
    agent_id = "SCOUT"
    domain = "recursive"
    description = "Capability Research — monitors arXiv, GitHub, PubMed for actionable discoveries"
    supported_task_types = ["research", "quick_lookup", "summarization"]
    preferred_model = "chatjimmy"

    async def sweep(self, domains: list[str]) -> list[Discovery]:
        """Parallel poll of all sources within the given domains."""
        tasks = [self._poll_source(src) for src in SOURCES if src["domain"] in domains]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        discoveries: list[Discovery] = []
        for r in results:
            if isinstance(r, list):
                discoveries.extend(r)
        return discoveries

    async def _poll_source(self, source: dict) -> list[Discovery]:
        """Poll a single source. Returns a synthetic discovery for now (no live web calls)."""
        # In production: httpx GET → parse → triage with ChatJimmy → escalate to Claude if relevance > 0.6
        # Stub: return a synthetic discovery for testability
        return [
            Discovery(
                source_name=source["name"],
                title=f"[{source['name']}] Recent advances in AI",
                url=source["url"],
                summary=f"Synthetic discovery from {source['name']} — awaiting live feed integration.",
                domain=source["domain"],
                relevance_score=0.7,
                actionable=True,
            )
        ]

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        domains = context.get("domains", ["scientific", "recursive"])
        discoveries = await self.sweep(domains)
        summary = f"SCOUT found {len(discoveries)} discoveries:\n"
        for d in discoveries:
            summary += f"- [{d.source_name}] {d.title} (relevance={d.relevance_score:.2f})\n"

        # Write to HyperMemory
        for d in discoveries:
            try:
                await self.causal_graph.add_node(
                    label=d.title,
                    node_type="discovery",
                    domain=d.domain,
                    metadata={
                        "source": d.source_name,
                        "url": d.url,
                        "relevance_score": d.relevance_score,
                        "discovery_id": str(d.discovery_id),
                    },
                )
                d.added_to_knowledge_graph = True
            except Exception:
                pass

        await self.log_completion(state, summary, "chatjimmy", True)
        return summary
