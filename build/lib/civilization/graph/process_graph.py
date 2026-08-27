"""
Process graph for representing workflows, SOPs, and procedural knowledge.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator
from uuid import UUID, uuid4

from ..schema import SOP, SOPStep, Workflow, WorkflowNode, WorkflowEdge, Checklist

logger = logging.getLogger(__name__)


class ProcessNodeType(str, Enum):
    START = "start"
    END = "end"
    TASK = "task"
    DECISION = "decision"
    APPROVAL = "approval"
    SUBPROCESS = "subprocess"
    PARALLEL_GATEWAY = "parallel_gateway"
    JOIN_GATEWAY = "join_gateway"
    EVENT = "event"
    WAIT = "wait"


class ProcessEdgeType(str, Enum):
    SEQUENCE = "sequence"
    CONDITIONAL = "conditional"
    DEFAULT = "default"
    EXCEPTION = "exception"
    TIMEOUT = "timeout"


@dataclass
class ProcessNode:
    """A node in the process graph."""
    id: str
    node_type: ProcessNodeType
    label: str
    description: str | None = None
    responsible_role: str | None = None
    tools: list[str] = field(default_factory=list)
    duration_minutes: int | None = None
    sla_minutes: int | None = None
    metadata: dict = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)


@dataclass
class ProcessEdge:
    """An edge connecting two process nodes."""
    source_id: str
    target_id: str
    edge_type: ProcessEdgeType = ProcessEdgeType.SEQUENCE
    condition: str | None = None
    probability: float | None = None  # For decision edges
    metadata: dict = field(default_factory=dict)

    def __hash__(self):
        return hash((self.source_id, self.target_id))


class ProcessGraph:
    """
    Graph representation of a process or workflow.
    Supports path analysis, critical path detection, and process validation.
    """

    def __init__(self, process_id: UUID, name: str):
        self.process_id = process_id
        self.name = name
        self._nodes: dict[str, ProcessNode] = {}
        self._edges: list[ProcessEdge] = []
        self._adjacency: dict[str, list[ProcessEdge]] = {}
        self._reverse_adjacency: dict[str, list[ProcessEdge]] = {}

    def add_node(self, node: ProcessNode) -> None:
        """Add a node to the graph."""
        self._nodes[node.id] = node
        if node.id not in self._adjacency:
            self._adjacency[node.id] = []
        if node.id not in self._reverse_adjacency:
            self._reverse_adjacency[node.id] = []

    def add_edge(self, edge: ProcessEdge) -> None:
        """Add an edge to the graph."""
        if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
            raise ValueError("Both source and target nodes must exist in the graph")
        self._edges.append(edge)
        self._adjacency[edge.source_id].append(edge)
        self._reverse_adjacency[edge.target_id].append(edge)

    def get_node(self, node_id: str) -> ProcessNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_start_nodes(self) -> list[ProcessNode]:
        """Get all start nodes."""
        return [n for n in self._nodes.values() if n.node_type == ProcessNodeType.START]

    def get_end_nodes(self) -> list[ProcessNode]:
        """Get all end nodes."""
        return [n for n in self._nodes.values() if n.node_type == ProcessNodeType.END]

    def get_successors(self, node_id: str) -> list[ProcessNode]:
        """Get all successor nodes."""
        edges = self._adjacency.get(node_id, [])
        return [self._nodes[e.target_id] for e in edges if e.target_id in self._nodes]

    def get_predecessors(self, node_id: str) -> list[ProcessNode]:
        """Get all predecessor nodes."""
        edges = self._reverse_adjacency.get(node_id, [])
        return [self._nodes[e.source_id] for e in edges if e.source_id in self._nodes]

    def get_decision_nodes(self) -> list[ProcessNode]:
        """Get all decision nodes."""
        return [n for n in self._nodes.values() if n.node_type == ProcessNodeType.DECISION]

    def find_all_paths(
        self,
        start_id: str | None = None,
        end_id: str | None = None,
    ) -> list[list[str]]:
        """Find all paths from start to end nodes."""
        starts = [start_id] if start_id else [n.id for n in self.get_start_nodes()]
        ends = set([end_id]) if end_id else set(n.id for n in self.get_end_nodes())

        all_paths = []

        for start in starts:
            stack = [(start, [start])]
            while stack:
                current, path = stack.pop()
                if current in ends:
                    all_paths.append(path)
                    continue
                for edge in self._adjacency.get(current, []):
                    if edge.target_id not in path:  # Avoid cycles
                        stack.append((edge.target_id, path + [edge.target_id]))

        return all_paths

    def calculate_critical_path(self) -> list[ProcessNode]:
        """
        Calculate the critical path (longest path through the process).
        Uses node durations if available.
        """
        all_paths = self.find_all_paths()
        if not all_paths:
            return []

        def path_duration(path: list[str]) -> int:
            total = 0
            for node_id in path:
                node = self._nodes.get(node_id)
                if node and node.duration_minutes:
                    total += node.duration_minutes
            return total

        critical = max(all_paths, key=path_duration)
        return [self._nodes[node_id] for node_id in critical]

    def estimate_total_duration(self) -> int:
        """Estimate total process duration based on critical path."""
        critical = self.calculate_critical_path()
        return sum(n.duration_minutes or 0 for n in critical)

    def validate(self) -> list[str]:
        """
        Validate the process graph structure.
        Returns list of validation issues.
        """
        issues = []

        # Check for start node
        starts = self.get_start_nodes()
        if not starts:
            issues.append("Process has no START node")
        elif len(starts) > 1:
            issues.append(f"Process has {len(starts)} START nodes (expected 1)")

        # Check for end node
        ends = self.get_end_nodes()
        if not ends:
            issues.append("Process has no END node")

        # Check for unreachable nodes
        if starts:
            reachable = self._get_reachable_nodes(starts[0].id)
            unreachable = set(self._nodes.keys()) - reachable
            if unreachable:
                issues.append(f"Unreachable nodes: {unreachable}")

        # Check for dead ends (non-end nodes with no outgoing edges)
        for node_id, node in self._nodes.items():
            if node.node_type != ProcessNodeType.END:
                if not self._adjacency.get(node_id):
                    issues.append(f"Dead end: {node_id} ({node.label})")

        # Check for cycles
        if self._has_cycle():
            issues.append("Process contains a cycle")

        return issues

    def _get_reachable_nodes(self, start_id: str) -> set[str]:
        """Get all nodes reachable from a starting node."""
        visited = set()
        stack = [start_id]

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for edge in self._adjacency.get(current, []):
                stack.append(edge.target_id)

        return visited

    def _has_cycle(self) -> bool:
        """Check if graph has any cycles using DFS."""
        visited = set()
        rec_stack = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for edge in self._adjacency.get(node_id, []):
                if edge.target_id not in visited:
                    if dfs(edge.target_id):
                        return True
                elif edge.target_id in rec_stack:
                    return True

            rec_stack.remove(node_id)
            return False

        for node_id in self._nodes:
            if node_id not in visited:
                if dfs(node_id):
                    return True

        return False

    def get_roles_involved(self) -> list[str]:
        """Get all roles involved in the process."""
        roles = set()
        for node in self._nodes.values():
            if node.responsible_role:
                roles.add(node.responsible_role)
        return list(roles)

    def get_tools_used(self) -> list[str]:
        """Get all tools used in the process."""
        tools = set()
        for node in self._nodes.values():
            tools.update(node.tools)
        return list(tools)

    def get_node_by_role(self, role: str) -> list[ProcessNode]:
        """Get all nodes assigned to a specific role."""
        return [n for n in self._nodes.values() if n.responsible_role == role]

    def to_dict(self) -> dict:
        """Export graph to dictionary format."""
        return {
            "process_id": str(self.process_id),
            "name": self.name,
            "nodes": [
                {
                    "id": n.id,
                    "node_type": n.node_type.value,
                    "label": n.label,
                    "description": n.description,
                    "responsible_role": n.responsible_role,
                    "tools": n.tools,
                    "duration_minutes": n.duration_minutes,
                    "metadata": n.metadata,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "edge_type": e.edge_type.value,
                    "condition": e.condition,
                    "metadata": e.metadata,
                }
                for e in self._edges
            ],
        }

    @classmethod
    def from_sop(cls, sop: SOP) -> "ProcessGraph":
        """Build a ProcessGraph from an SOP."""
        graph = cls(process_id=sop.id, name=sop.title)

        # Add start node
        start = ProcessNode(
            id="start",
            node_type=ProcessNodeType.START,
            label="Start",
            description=sop.purpose,
        )
        graph.add_node(start)

        # Add step nodes
        prev_id = "start"
        for step in sop.steps:
            step_id = f"step_{step.step_number}"
            node = ProcessNode(
                id=step_id,
                node_type=ProcessNodeType.TASK,
                label=step.title,
                description=step.description,
                responsible_role=step.responsible_role,
                tools=step.tools_required,
                duration_minutes=step.estimated_duration_minutes,
            )
            graph.add_node(node)
            graph.add_edge(ProcessEdge(source_id=prev_id, target_id=step_id))
            prev_id = step_id

        # Add end node
        end = ProcessNode(
            id="end",
            node_type=ProcessNodeType.END,
            label="End",
        )
        graph.add_node(end)
        graph.add_edge(ProcessEdge(source_id=prev_id, target_id="end"))

        return graph

    @classmethod
    def from_workflow(cls, workflow: Workflow) -> "ProcessGraph":
        """Build a ProcessGraph from a Workflow node."""
        graph = cls(process_id=workflow.id, name=workflow.title)

        # Map workflow node types to process node types
        type_map = {
            "start": ProcessNodeType.START,
            "end": ProcessNodeType.END,
            "task": ProcessNodeType.TASK,
            "decision": ProcessNodeType.DECISION,
            "approval": ProcessNodeType.APPROVAL,
        }

        # Add nodes
        for wf_node in workflow.nodes:
            node = ProcessNode(
                id=wf_node.node_id,
                node_type=type_map.get(wf_node.node_type, ProcessNodeType.TASK),
                label=wf_node.label,
                description=wf_node.description,
                responsible_role=wf_node.responsible_role,
                tools=[wf_node.tool] if wf_node.tool else [],
            )
            graph.add_node(node)

        # Add edges
        for wf_edge in workflow.edges:
            edge_type = ProcessEdgeType.CONDITIONAL if wf_edge.condition else ProcessEdgeType.SEQUENCE
            edge = ProcessEdge(
                source_id=wf_edge.from_node,
                target_id=wf_edge.to_node,
                edge_type=edge_type,
                condition=wf_edge.condition,
            )
            graph.add_edge(edge)

        return graph

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[ProcessNode]:
        return iter(self._nodes.values())
