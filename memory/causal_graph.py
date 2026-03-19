"""
HyperMemory Causal Graph — tracks causal relationships between agent actions and outcomes.
Stub for v0.1.0-alpha.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class CausalNode:
    node_id: str
    action: str
    outcome: str
    children: list[str] = field(default_factory=list)


class CausalGraph:
    """Tracks cause-effect chains across agent decisions. Stub for v0.1.0-alpha."""

    def __init__(self) -> None:
        self._nodes: dict[str, CausalNode] = {}

    def add_node(self, node: CausalNode) -> None:
        self._nodes[node.node_id] = node

    def get_node(self, node_id: str) -> CausalNode | None:
        return self._nodes.get(node_id)
