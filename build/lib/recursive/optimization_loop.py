"""
OptimizationLoop — analyzes SwarmMessage history and identifies routing inefficiencies.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from core.hyperrouter.bandit import HyperRouter
    from memory.causal_graph import CausalGraph

log = logging.getLogger("hyperclaw.optimization_loop")


class OptimizationLoop:
    """
    Reads SwarmMessage history, identifies inefficiencies, proposes optimizations.
    Writes findings to CausalGraph as outcome nodes.
    """

    def __init__(self, causal_graph: "CausalGraph | None" = None) -> None:
        self._causal_graph = causal_graph

    async def analyze(
        self,
        pool: asyncpg.Pool,
        hyper_router: "HyperRouter",
    ) -> dict:
        """
        Read last 7 days of swarm_messages.
        Identify: over-routed expensive models, underperforming agents, low-cert domains.
        Returns optimization_report dict.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT model_used, task_type, certified, cost_usd, agent_id
                    FROM swarm_messages
                    WHERE created_at > $1
                    """,
                    cutoff,
                )
        except Exception as e:
            log.warning(f"OptimizationLoop: DB read failed: {e}")
            rows = []

        if not rows:
            report = {
                "messages_analyzed": 0,
                "expensive_model_overuse": [],
                "underperforming_agents": [],
                "low_cert_domains": [],
                "recommendations": ["No data available — run more tasks to generate insights."],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return report

        # Analyze
        model_costs: dict[str, float] = {}
        agent_cert: dict[str, list[bool]] = {}
        task_cert: dict[str, list[bool]] = {}

        for row in rows:
            model = row["model_used"]
            agent = row["agent_id"]
            certified = bool(row["certified"])
            cost = float(row["cost_usd"] or 0)

            model_costs[model] = model_costs.get(model, 0) + cost
            agent_cert.setdefault(agent, []).append(certified)
            task_cert.setdefault(row["task_type"], []).append(certified)

        # Expensive models
        expensive = [
            {"model": m, "total_cost_usd": c}
            for m, c in model_costs.items()
            if c > 1.0  # more than $1 in 7 days
        ]

        # Underperforming agents (cert rate < 30%)
        underperforming = [
            {"agent_id": a, "cert_rate": sum(certs) / len(certs)}
            for a, certs in agent_cert.items()
            if len(certs) >= 3 and sum(certs) / len(certs) < 0.3
        ]

        # Low cert domains
        low_cert = [
            {"task_type": t, "cert_rate": sum(c) / len(c)}
            for t, c in task_cert.items()
            if len(c) >= 5 and sum(c) / len(c) < 0.2
        ]

        recommendations = []
        if expensive:
            recommendations.append(f"Consider routing more tasks to ChatJimmy for eligible task types.")
        if underperforming:
            recommendations.append(f"Review agents: {[a['agent_id'] for a in underperforming]}")
        if not recommendations:
            recommendations.append("System operating efficiently.")

        report = {
            "messages_analyzed": len(rows),
            "expensive_model_overuse": expensive,
            "underperforming_agents": underperforming,
            "low_cert_domains": low_cert,
            "recommendations": recommendations,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Write to CausalGraph
        if self._causal_graph:
            try:
                await self._causal_graph.add_node(
                    label=f"optimization_report: {len(rows)} messages analyzed",
                    node_type="outcome",
                    domain="recursive",
                    metadata={"report": report},
                )
            except Exception as e:
                log.warning(f"OptimizationLoop: CausalGraph write failed: {e}")

        log.info(f"OptimizationLoop: analyzed {len(rows)} messages, {len(recommendations)} recommendations")
        return report
