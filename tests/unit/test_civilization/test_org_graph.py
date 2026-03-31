"""
Unit tests for civilization/graph/ — org graph, process graph, knowledge linker.
"""
from __future__ import annotations

import pytest
from uuid import uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import Person, Role, OrgChart, OrgUnit, SOP, SOPStep, Workflow, WorkflowNode, WorkflowEdge
from civilization.graph.org_graph import OrgGraph, OrgNode, OrgEdge, OrgNodeType, OrgEdgeType
from civilization.graph.process_graph import ProcessGraph, ProcessNode, ProcessEdge, ProcessNodeType, ProcessEdgeType
from civilization.graph.knowledge_linker import KnowledgeLinker, KnowledgeLink, LinkType


class TestOrgGraph:
    @pytest.fixture
    def graph(self):
        return OrgGraph(org_id="org_test")

    @pytest.fixture
    def populated_graph(self):
        graph = OrgGraph(org_id="org_test")
        # Create hierarchy: CEO -> VP -> Manager -> Engineer
        ceo = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="CEO", level=0)
        vp = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="VP", level=1)
        mgr = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="Manager", level=2)
        eng = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="Engineer", level=3)

        for node in [ceo, vp, mgr, eng]:
            graph.add_node(node)

        graph.add_edge(OrgEdge(source_id=vp.id, target_id=ceo.id, edge_type=OrgEdgeType.REPORTS_TO))
        graph.add_edge(OrgEdge(source_id=mgr.id, target_id=vp.id, edge_type=OrgEdgeType.REPORTS_TO))
        graph.add_edge(OrgEdge(source_id=eng.id, target_id=mgr.id, edge_type=OrgEdgeType.REPORTS_TO))

        graph._test_nodes = {"ceo": ceo, "vp": vp, "mgr": mgr, "eng": eng}
        return graph

    def test_add_node(self, graph):
        node = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="Test")
        graph.add_node(node)
        assert graph.get_node(node.id) == node

    def test_add_edge(self, graph):
        n1 = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="A")
        n2 = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="B")
        graph.add_node(n1)
        graph.add_node(n2)

        edge = OrgEdge(source_id=n1.id, target_id=n2.id, edge_type=OrgEdgeType.REPORTS_TO)
        graph.add_edge(edge)

        edges = graph.get_edges_from(n1.id)
        assert len(edges) == 1

    def test_add_edge_invalid_nodes(self, graph):
        with pytest.raises(ValueError):
            graph.add_edge(OrgEdge(
                source_id=uuid4(),
                target_id=uuid4(),
                edge_type=OrgEdgeType.REPORTS_TO,
            ))

    def test_get_manager(self, populated_graph):
        eng = populated_graph._test_nodes["eng"]
        mgr = populated_graph._test_nodes["mgr"]

        manager = populated_graph.get_manager(eng.id)
        assert manager is not None
        assert manager.id == mgr.id

    def test_get_direct_reports(self, populated_graph):
        mgr = populated_graph._test_nodes["mgr"]
        eng = populated_graph._test_nodes["eng"]

        reports = populated_graph.get_direct_reports(mgr.id)
        assert len(reports) == 1
        assert reports[0].id == eng.id

    def test_get_reporting_chain(self, populated_graph):
        eng = populated_graph._test_nodes["eng"]
        chain = populated_graph.get_reporting_chain(eng.id)
        assert len(chain) == 3  # Manager, VP, CEO
        assert chain[0].name == "Manager"
        assert chain[2].name == "CEO"

    def test_find_common_manager(self, populated_graph):
        # Add another engineer under same manager
        mgr = populated_graph._test_nodes["mgr"]
        eng2 = OrgNode(id=uuid4(), node_type=OrgNodeType.PERSON, name="Engineer2")
        populated_graph.add_node(eng2)
        populated_graph.add_edge(OrgEdge(
            source_id=eng2.id,
            target_id=mgr.id,
            edge_type=OrgEdgeType.REPORTS_TO,
        ))

        eng1 = populated_graph._test_nodes["eng"]
        common = populated_graph.find_common_manager(eng1.id, eng2.id)
        assert common is not None
        assert common.id == mgr.id

    def test_calculate_distance(self, populated_graph):
        eng = populated_graph._test_nodes["eng"]
        ceo = populated_graph._test_nodes["ceo"]

        distance = populated_graph.calculate_distance(eng.id, ceo.id)
        assert distance == 3

    def test_calculate_span_of_control(self, populated_graph):
        mgr = populated_graph._test_nodes["mgr"]
        span = populated_graph.calculate_span_of_control(mgr.id)
        assert span == 1

    def test_to_dict(self, populated_graph):
        data = populated_graph.to_dict()
        assert "org_id" in data
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 4
        assert len(data["edges"]) == 3

    def test_from_people(self):
        ceo_id = uuid4()
        vp_id = uuid4()

        people = [
            Person(org_id="o", title="CEO", name="Jane CEO", id=ceo_id),
            Person(org_id="o", title="VP", name="John VP", id=vp_id, manager_id=ceo_id),
        ]
        graph = OrgGraph.from_people("o", people)
        assert len(graph) == 2

        manager = graph.get_manager(vp_id)
        assert manager is not None
        assert manager.id == ceo_id


class TestProcessGraph:
    @pytest.fixture
    def graph(self):
        graph = ProcessGraph(process_id=uuid4(), name="Test Process")

        start = ProcessNode(id="start", node_type=ProcessNodeType.START, label="Start")
        step1 = ProcessNode(id="step1", node_type=ProcessNodeType.TASK, label="Step 1", duration_minutes=30)
        step2 = ProcessNode(id="step2", node_type=ProcessNodeType.TASK, label="Step 2", duration_minutes=60)
        end = ProcessNode(id="end", node_type=ProcessNodeType.END, label="End")

        for node in [start, step1, step2, end]:
            graph.add_node(node)

        graph.add_edge(ProcessEdge(source_id="start", target_id="step1"))
        graph.add_edge(ProcessEdge(source_id="step1", target_id="step2"))
        graph.add_edge(ProcessEdge(source_id="step2", target_id="end"))

        return graph

    def test_get_start_nodes(self, graph):
        starts = graph.get_start_nodes()
        assert len(starts) == 1
        assert starts[0].id == "start"

    def test_get_end_nodes(self, graph):
        ends = graph.get_end_nodes()
        assert len(ends) == 1
        assert ends[0].id == "end"

    def test_get_successors(self, graph):
        successors = graph.get_successors("step1")
        assert len(successors) == 1
        assert successors[0].id == "step2"

    def test_get_predecessors(self, graph):
        predecessors = graph.get_predecessors("step2")
        assert len(predecessors) == 1
        assert predecessors[0].id == "step1"

    def test_find_all_paths(self, graph):
        paths = graph.find_all_paths()
        assert len(paths) == 1
        assert paths[0] == ["start", "step1", "step2", "end"]

    def test_calculate_critical_path(self, graph):
        critical = graph.calculate_critical_path()
        assert len(critical) == 4

    def test_estimate_total_duration(self, graph):
        duration = graph.estimate_total_duration()
        assert duration == 90  # 30 + 60

    def test_validate_valid_graph(self, graph):
        issues = graph.validate()
        assert len(issues) == 0

    def test_validate_no_start(self):
        graph = ProcessGraph(process_id=uuid4(), name="Bad")
        graph.add_node(ProcessNode(id="task", node_type=ProcessNodeType.TASK, label="Task"))
        graph.add_node(ProcessNode(id="end", node_type=ProcessNodeType.END, label="End"))
        graph.add_edge(ProcessEdge(source_id="task", target_id="end"))

        issues = graph.validate()
        assert any("START" in issue for issue in issues)

    def test_from_sop(self):
        sop = SOP(
            org_id="o",
            title="Test SOP",
            purpose="Purpose",
            scope="Scope",
            steps=[
                SOPStep(step_number=1, title="Step 1", description="Do 1"),
                SOPStep(step_number=2, title="Step 2", description="Do 2"),
            ],
        )
        graph = ProcessGraph.from_sop(sop)
        assert len(graph) == 4  # start + 2 steps + end
        assert len(graph.get_start_nodes()) == 1
        assert len(graph.get_end_nodes()) == 1

    def test_from_workflow(self):
        workflow = Workflow(
            org_id="o",
            title="Test Workflow",
            description="Desc",
            nodes=[
                WorkflowNode(node_id="start", label="Start", node_type="start"),
                WorkflowNode(node_id="task", label="Task", node_type="task"),
                WorkflowNode(node_id="end", label="End", node_type="end"),
            ],
            edges=[
                WorkflowEdge(from_node="start", to_node="task"),
                WorkflowEdge(from_node="task", to_node="end"),
            ],
        )
        graph = ProcessGraph.from_workflow(workflow)
        assert len(graph) == 3


class TestKnowledgeLinker:
    @pytest.fixture
    def linker(self):
        return KnowledgeLinker(org_id="org_test")

    def test_create_link(self, linker):
        link = linker.create_link(
            source_id=uuid4(),
            target_id=uuid4(),
            link_type=LinkType.REFERENCES,
            strength=0.8,
        )
        assert isinstance(link, KnowledgeLink)
        assert link.link_type == LinkType.REFERENCES
        assert link.strength == 0.8

    def test_get_links_from(self, linker):
        source = uuid4()
        target = uuid4()
        linker.create_link(source, target, LinkType.OWNS)

        links = linker.get_links_from(source)
        assert len(links) == 1
        assert links[0].target_id == target

    def test_get_links_to(self, linker):
        source = uuid4()
        target = uuid4()
        linker.create_link(source, target, LinkType.OWNS)

        links = linker.get_links_to(target)
        assert len(links) == 1

    def test_link_inverse(self):
        link = KnowledgeLink(
            source_id=uuid4(),
            target_id=uuid4(),
            link_type=LinkType.PARENT_OF,
        )
        inverse = link.inverse()
        assert inverse.source_id == link.target_id
        assert inverse.target_id == link.source_id
        assert inverse.link_type == LinkType.CHILD_OF

    def test_detect_links(self, linker):
        role = Role(
            org_id="org_test",
            title="Manager Role",
            role_title="Account Manager",
            department="Sales",
            accountabilities=[],
            responsibilities=[],
            decision_authority=[],
            escalation_path=[],
            interfaces=[],
        )
        sop = SOP(
            org_id="org_test",
            title="Sales Process",
            purpose="Manage sales",
            scope="All",
            steps=[SOPStep(step_number=1, title="S", description="D")],
            roles_involved=["Account Manager"],
        )
        links = linker.detect_links([role, sop])
        # Should detect SOP -> Role link
        role_links = [l for l in links if l.link_type == LinkType.REQUIRES_ROLE]
        assert len(role_links) > 0

    def test_get_link_statistics(self, linker):
        linker.create_link(uuid4(), uuid4(), LinkType.REFERENCES)
        linker.create_link(uuid4(), uuid4(), LinkType.OWNS)

        stats = linker.get_link_statistics()
        assert stats["total_links"] == 2
        assert "by_type" in stats
