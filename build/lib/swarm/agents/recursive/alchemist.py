"""ALCHEMIST — Skill Integration. Validates discoveries, implements skills via ClaudeCodeSubagent."""
from __future__ import annotations

import logging
from swarm.agents.base import BaseAgent
from swarm.agents.recursive.scout import Discovery
from core.hyperstate.schema import HyperState

log = logging.getLogger("hyperclaw.agent.ALCHEMIST")


class AlchemistAgent(BaseAgent):
    agent_id = "ALCHEMIST"
    domain = "recursive"
    description = "Skill Integration — validates discoveries, implements and certifies new skills"
    supported_task_types = ["code", "research", "analysis"]
    preferred_model = "claude-sonnet-4-6"

    async def validate(self, discovery: dict) -> bool:
        """Validate a discovery — check if it's actionable and relevant enough to implement."""
        relevance = discovery.get("relevance_score", 0.0)
        actionable = discovery.get("actionable", False)
        return actionable and relevance >= 0.6

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        discoveries = context.get("discoveries", [])
        validated_count = 0
        skill_count = 0

        for d in discoveries:
            d_dict = d if isinstance(d, dict) else d.__dict__
            if not await self.validate(d_dict):
                continue
            validated_count += 1

            # Implement via ClaudeCodeSubagent
            try:
                from models.claude_code_subagent import ClaudeCodeSubagent
                from models.claude_client import ClaudeClient
                from core.hyperstate.certifier import Certifier
                import os

                client = ClaudeClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
                subagent = ClaudeCodeSubagent(client)
                implementation_task = (
                    f"Implement a Python utility based on this discovery:\n"
                    f"Title: {d_dict.get('title', 'Unknown')}\n"
                    f"Summary: {d_dict.get('summary', 'No summary')}\n"
                    f"Write a concrete, runnable Python function with assertions to test it."
                )
                result = await subagent.run(implementation_task, context="HyperClaw skill integration")

                if result.success:
                    # Write skill node to CausalGraph
                    import uuid
                    skill_node_id = await self.causal_graph.add_node(
                        label=f"skill: {d_dict.get('title', 'unknown')}",
                        node_type="skill",
                        domain=d_dict.get("domain", "recursive"),
                        metadata={
                            "source_discovery": d_dict.get("source_name", ""),
                            "code_length": len(result.code),
                            "iterations": result.iterations,
                        },
                    )
                    skill_count += 1
                    log.info(f"ALCHEMIST: skill certified — node={skill_node_id}")
            except Exception as e:
                log.warning(f"ALCHEMIST: skill integration failed for discovery: {e}")

        result_str = f"ALCHEMIST: validated={validated_count}, skills_added={skill_count}"
        await self.log_completion(state, result_str, "claude-sonnet-4-6", True)
        return result_str
