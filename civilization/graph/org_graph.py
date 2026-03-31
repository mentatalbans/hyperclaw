"""
Organizational graph for representing reporting structures, teams, and hierarchies.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator
from uuid import UUID, uuid4

from ..schema import Person, Role, OrgChart, OrgUnit

logger = logging.getLogger(__name__)


class OrgNodeType(str, Enum):
    PERSON = "person"
    ROLE = "role"
    TEAM = "team"
    DEPARTMENT = "department"
    DIVISION = "division"
    ORGANIZATION = "organization"


class OrgEdgeType(str, Enum):
    REPORTS_TO = "reports_to"
    MANAGES = "manages"
    MEMBER_OF = "member_of"
    LEADS = "leads"
    COLLABORATES_WITH = "collaborates_with"
    DOTTED_LINE_TO = "dotted_line_to"


@dataclass
class OrgNode:
    """A node in the organizational graph."""
    id: UUID
    node_type: OrgNodeType
    name: str
    title: str | None = None
    department: str | None = None
    level: int = 0  # Hierarchy level (0 = root)
    metadata: dict = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)


@dataclass
class OrgEdge:
    """An edge connecting two organizational nodes."""
    source_id: UUID
    target_id: UUID
    edge_type: OrgEdgeType
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)

    def __hash__(self):
        return hash((self.source_id, self.target_id, self.edge_type))


class OrgGraph:
    """
    Graph representation of organizational structure.
    Supports hierarchy traversal, team composition queries, and path finding.
    """

    def __init__(self, org_id: str):
        self.org_id = org_id
        self._nodes: dict[UUID, OrgNode] = {}
        self._edges: list[OrgEdge] = []
        self._adjacency: dict[UUID, list[OrgEdge]] = {}
        self._reverse_adjacency: dict[UUID, list[OrgEdge]] = {}

    def add_node(self, node: OrgNode) -> None:
        """Add a node to the graph."""
        self._nodes[node.id] = node
        if node.id not in self._adjacency:
            self._adjacency[node.id] = []
        if node.id not in self._reverse_adjacency:
            self._reverse_adjacency[node.id] = []

    def add_edge(self, edge: OrgEdge) -> None:
        """Add an edge to the graph."""
        if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
            raise ValueError("Both source and target nodes must exist in the graph")
        self._edges.append(edge)
        self._adjacency[edge.source_id].append(edge)
        self._reverse_adjacency[edge.target_id].append(edge)

    def get_node(self, node_id: UUID) -> OrgNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_edges_from(self, node_id: UUID) -> list[OrgEdge]:
        """Get all edges originating from a node."""
        return self._adjacency.get(node_id, [])

    def get_edges_to(self, node_id: UUID) -> list[OrgEdge]:
        """Get all edges pointing to a node."""
        return self._reverse_adjacency.get(node_id, [])

    def get_manager(self, person_id: UUID) -> OrgNode | None:
        """Get the manager of a person."""
        edges = self.get_edges_from(person_id)
        for edge in edges:
            if edge.edge_type == OrgEdgeType.REPORTS_TO:
                return self.get_node(edge.target_id)
        return None

    def get_direct_reports(self, manager_id: UUID) -> list[OrgNode]:
        """Get all direct reports of a manager."""
        edges = self.get_edges_to(manager_id)
        reports = []
        for edge in edges:
            if edge.edge_type == OrgEdgeType.REPORTS_TO:
                node = self.get_node(edge.source_id)
                if node:
                    reports.append(node)
        return reports

    def get_team_members(self, team_id: UUID) -> list[OrgNode]:
        """Get all members of a team."""
        edges = self.get_edges_to(team_id)
        members = []
        for edge in edges:
            if edge.edge_type == OrgEdgeType.MEMBER_OF:
                node = self.get_node(edge.source_id)
                if node:
                    members.append(node)
        return members

    def get_reporting_chain(self, person_id: UUID) -> list[OrgNode]:
        """Get the full reporting chain from person to CEO."""
        chain = []
        current = self.get_node(person_id)
        visited = set()

        while current and current.id not in visited:
            visited.add(current.id)
            manager = self.get_manager(current.id)
            if manager:
                chain.append(manager)
                current = manager
            else:
                break

        return chain

    def get_all_descendants(self, node_id: UUID) -> list[OrgNode]:
        """Get all descendants (direct and indirect reports) of a node."""
        descendants = []
        to_visit = [node_id]
        visited = set()

        while to_visit:
            current_id = to_visit.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            direct_reports = self.get_direct_reports(current_id)
            for report in direct_reports:
                descendants.append(report)
                to_visit.append(report.id)

        return descendants

    def find_common_manager(self, person1_id: UUID, person2_id: UUID) -> OrgNode | None:
        """Find the lowest common manager of two people."""
        chain1 = set([person1_id] + [n.id for n in self.get_reporting_chain(person1_id)])
        chain2 = self.get_reporting_chain(person2_id)

        for manager in chain2:
            if manager.id in chain1:
                return manager

        # Check if person1 is in person2's chain
        if person1_id in set(n.id for n in chain2):
            return self.get_node(person1_id)

        return None

    def calculate_distance(self, node1_id: UUID, node2_id: UUID) -> int | None:
        """Calculate organizational distance between two nodes (BFS)."""
        if node1_id == node2_id:
            return 0

        visited = {node1_id}
        queue = [(node1_id, 0)]

        while queue:
            current_id, distance = queue.pop(0)

            # Check all adjacent nodes (both directions)
            neighbors = []
            for edge in self.get_edges_from(current_id):
                neighbors.append(edge.target_id)
            for edge in self.get_edges_to(current_id):
                neighbors.append(edge.source_id)

            for neighbor_id in neighbors:
                if neighbor_id == node2_id:
                    return distance + 1
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, distance + 1))

        return None  # No path found

    def get_department_members(self, department: str) -> list[OrgNode]:
        """Get all nodes in a department."""
        return [n for n in self._nodes.values() if n.department == department]

    def get_nodes_by_type(self, node_type: OrgNodeType) -> list[OrgNode]:
        """Get all nodes of a specific type."""
        return [n for n in self._nodes.values() if n.node_type == node_type]

    def calculate_span_of_control(self, manager_id: UUID) -> int:
        """Calculate span of control (number of direct reports)."""
        return len(self.get_direct_reports(manager_id))

    def calculate_depth(self) -> int:
        """Calculate maximum depth of the org hierarchy."""
        max_depth = 0
        for node in self._nodes.values():
            chain = self.get_reporting_chain(node.id)
            max_depth = max(max_depth, len(chain) + 1)
        return max_depth

    def to_dict(self) -> dict:
        """Export graph to dictionary format."""
        return {
            "org_id": self.org_id,
            "nodes": [
                {
                    "id": str(n.id),
                    "node_type": n.node_type.value,
                    "name": n.name,
                    "title": n.title,
                    "department": n.department,
                    "level": n.level,
                    "metadata": n.metadata,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source_id": str(e.source_id),
                    "target_id": str(e.target_id),
                    "edge_type": e.edge_type.value,
                    "weight": e.weight,
                    "metadata": e.metadata,
                }
                for e in self._edges
            ],
        }

    @classmethod
    def from_org_chart(cls, org_chart: OrgChart) -> "OrgGraph":
        """Build an OrgGraph from an OrgChart node."""
        graph = cls(org_id=org_chart.org_id)

        # Add org root
        org_node = OrgNode(
            id=org_chart.id,
            node_type=OrgNodeType.ORGANIZATION,
            name=org_chart.organization_name,
            level=0,
        )
        graph.add_node(org_node)

        # Process units
        unit_nodes: dict[str, OrgNode] = {}
        for unit in org_chart.units:
            unit_type_map = {
                "division": OrgNodeType.DIVISION,
                "department": OrgNodeType.DEPARTMENT,
                "team": OrgNodeType.TEAM,
            }
            unit_node = OrgNode(
                id=uuid4(),
                node_type=unit_type_map.get(unit.unit_type, OrgNodeType.TEAM),
                name=unit.name,
                metadata={"unit_id": unit.unit_id, "mission": unit.mission},
            )
            graph.add_node(unit_node)
            unit_nodes[unit.unit_id] = unit_node

        # Add hierarchy edges
        for unit in org_chart.units:
            unit_node = unit_nodes[unit.unit_id]
            if unit.parent_unit_id:
                parent = unit_nodes.get(unit.parent_unit_id)
                if parent:
                    graph.add_edge(OrgEdge(
                        source_id=unit_node.id,
                        target_id=parent.id,
                        edge_type=OrgEdgeType.MEMBER_OF,
                    ))
            else:
                # Top-level unit reports to org
                graph.add_edge(OrgEdge(
                    source_id=unit_node.id,
                    target_id=org_chart.id,
                    edge_type=OrgEdgeType.MEMBER_OF,
                ))

        return graph

    @classmethod
    def from_people(cls, org_id: str, people: list[Person]) -> "OrgGraph":
        """Build an OrgGraph from a list of Person nodes."""
        graph = cls(org_id=org_id)

        # Add all people as nodes
        for person in people:
            node = OrgNode(
                id=person.id,
                node_type=OrgNodeType.PERSON,
                name=person.name,
                title=person.department,
                department=person.department,
                metadata={
                    "email": person.email,
                    "location": person.location,
                    "timezone": person.timezone,
                },
            )
            graph.add_node(node)

        # Add reporting relationships
        person_map = {p.id: p for p in people}
        for person in people:
            if person.manager_id and person.manager_id in person_map:
                graph.add_edge(OrgEdge(
                    source_id=person.id,
                    target_id=person.manager_id,
                    edge_type=OrgEdgeType.REPORTS_TO,
                ))

        return graph

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[OrgNode]:
        return iter(self._nodes.values())
