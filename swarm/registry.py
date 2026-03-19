"""
AgentRegistry — central registry of all 20 HyperSwarm specialist agents.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from swarm.agents.base import BaseAgent
    from models.router import ModelRouter
    from core.hyperstate.state_manager import StateManager
    from memory.causal_graph import CausalGraph
    from security.hypershield import HyperShield

log = logging.getLogger("hyperclaw.registry")


class AgentRegistry:
    """Central registry of all HyperSwarm agents."""

    def __init__(self) -> None:
        self._agents: dict[str, "BaseAgent"] = {}

    def register(self, agent: "BaseAgent") -> None:
        self._agents[agent.agent_id] = agent
        log.debug(f"Registered agent: {agent.agent_id} ({agent.domain})")

    def get(self, agent_id: str) -> "BaseAgent":
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not registered")
        return self._agents[agent_id]

    def list_all(self) -> list["BaseAgent"]:
        return list(self._agents.values())

    def list_by_domain(self, domain: str) -> list["BaseAgent"]:
        return [a for a in self._agents.values() if a.domain == domain]

    def list_by_task_type(self, task_type: str) -> list["BaseAgent"]:
        return [a for a in self._agents.values() if task_type in a.supported_task_types]

    def as_dict(self) -> dict[str, "BaseAgent"]:
        return dict(self._agents)

    @classmethod
    def build_default(
        cls,
        model_router: "ModelRouter",
        state_manager: "StateManager",
        causal_graph: "CausalGraph",
        hyper_shield: "HyperShield",
    ) -> "AgentRegistry":
        """
        Instantiate and register all 20 specialist agents with shared dependencies.
        """
        registry = cls()
        deps = (model_router, state_manager, causal_graph, hyper_shield)

        # ── Personal ──────────────────────────────────────────────────────────
        from swarm.agents.personal.atlas import AtlasAgent
        from swarm.agents.personal.midas import MidasAgent
        from swarm.agents.personal.vitals import VitalsAgent
        from swarm.agents.personal.nourish import NourishAgent
        from swarm.agents.personal.navigator import NavigatorAgent
        from swarm.agents.personal.hearth import HearthAgent

        # ── Business ──────────────────────────────────────────────────────────
        from swarm.agents.business.strategos import StrategosAgent
        from swarm.agents.business.herald import HeraldAgent
        from swarm.agents.business.pipeline import PipelineAgent
        from swarm.agents.business.ledger import LedgerAgent
        from swarm.agents.business.counsel import CounselAgent
        from swarm.agents.business.talent import TalentAgent

        # ── Scientific ────────────────────────────────────────────────────────
        from swarm.agents.scientific.medicus import MedicusAgent
        from swarm.agents.scientific.cosmos import CosmosAgent
        from swarm.agents.scientific.gaia import GaiaAgent
        from swarm.agents.scientific.oracle_agent import OracleAgent
        from swarm.agents.scientific.scribe import ScribeAgent

        # ── Creative ──────────────────────────────────────────────────────────
        from swarm.agents.creative.author import AuthorAgent
        from swarm.agents.creative.lens import LensAgent

        # ── Recursive ─────────────────────────────────────────────────────────
        from swarm.agents.recursive.scout import ScoutAgent
        from swarm.agents.recursive.alchemist import AlchemistAgent
        from swarm.agents.recursive.calibrator import CalibratorAgent

        all_agents = [
            # Personal (6)
            AtlasAgent(*deps),
            MidasAgent(*deps),
            VitalsAgent(*deps),
            NourishAgent(*deps),
            NavigatorAgent(*deps),
            HearthAgent(*deps),
            # Business (6)
            StrategosAgent(*deps),
            HeraldAgent(*deps),
            PipelineAgent(*deps),
            LedgerAgent(*deps),
            CounselAgent(*deps),
            TalentAgent(*deps),
            # Scientific (5)
            MedicusAgent(*deps),
            CosmosAgent(*deps),
            GaiaAgent(*deps),
            OracleAgent(*deps),
            ScribeAgent(*deps),
            # Creative (2)
            AuthorAgent(*deps),
            LensAgent(*deps),
            # Recursive (3)
            ScoutAgent(*deps),
            AlchemistAgent(*deps),
            CalibratorAgent(*deps),
        ]

        for agent in all_agents:
            registry.register(agent)

        log.info(f"AgentRegistry: {len(registry._agents)} agents registered")
        return registry
