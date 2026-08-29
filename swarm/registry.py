"""
AgentRegistry — HyperSwarm Registry
Specialist AI agents with shared dependencies.
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
        Instantiate and register all specialist agents with shared dependencies.
        """
        registry = cls()
        deps = (model_router, state_manager, causal_graph, hyper_shield)

        # Discover every agent dynamically — a hand-maintained import list
        # silently drops agents added later (VENTURE, trading, intelligence
        # were all missing from the old list).
        import importlib
        import inspect
        import pkgutil

        import swarm.agents as _pkg
        from swarm.agents.base import BaseAgent

        for mod_info in pkgutil.walk_packages(_pkg.__path__, _pkg.__name__ + "."):
            try:
                mod = importlib.import_module(mod_info.name)
            except Exception as e:
                log.warning(f"Agent module {mod_info.name} failed to import: {e}")
                continue
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if not (issubclass(obj, BaseAgent) and obj is not BaseAgent
                        and obj.__module__ == mod.__name__):
                    continue
                try:
                    agent = obj(*deps)
                except TypeError:
                    # Older agents take no shared deps — construct bare and
                    # attach the deps they understand.
                    try:
                        agent = obj()
                        for attr, val in zip(
                            ("model_router", "state_manager", "causal_graph", "hyper_shield"),
                            deps,
                        ):
                            if not hasattr(agent, attr) or getattr(agent, attr) is None:
                                setattr(agent, attr, val)
                    except Exception as e:
                        log.warning(f"{obj.__name__} failed to initialize: {e}")
                        continue
                except Exception as e:
                    log.warning(f"{obj.__name__} failed to initialize: {e}")
                    continue
                if agent.agent_id in registry._agents:
                    # Never silently overwrite — qualify with the domain.
                    alt = f"{agent.agent_id}-{agent.domain.upper()}"
                    log.warning(
                        f"agent_id collision: '{agent.agent_id}' already registered "
                        f"({registry._agents[agent.agent_id].__class__.__name__}); "
                        f"registering {obj.__name__} as '{alt}'"
                    )
                    agent.agent_id = alt
                registry.register(agent)

        log.info(f"AgentRegistry: {len(registry._agents)} agents registered — HyperSwarm ONLINE")
        return registry
