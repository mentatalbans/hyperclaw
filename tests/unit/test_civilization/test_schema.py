"""
Unit tests for civilization/schema.py — Pydantic models for Civilization nodes.
"""
from __future__ import annotations

import pytest
from datetime import datetime
from uuid import UUID, uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import (
    NodeType, NodeStatus, CivilizationNode,
    SOP, SOPStep, JobDescription, Role, Person,
    Checklist, ChecklistItem, Runbook, RunbookStep,
    Workflow, WorkflowNode, WorkflowEdge, OrgChart, OrgUnit,
    ClientProfile, PersonalRoutine, RoutineBlock,
)


class TestNodeTypes:
    def test_all_node_types_exist(self):
        assert NodeType.SOP == "sop"
        assert NodeType.JOB_DESCRIPTION == "job_description"
        assert NodeType.CHECKLIST == "checklist"
        assert NodeType.RUNBOOK == "runbook"
        assert NodeType.WORKFLOW == "workflow"
        assert NodeType.ORG_CHART == "org_chart"
        assert NodeType.ROLE == "role"
        assert NodeType.PERSON == "person"
        assert NodeType.CLIENT_PROFILE == "client_profile"
        assert NodeType.PERSONAL_ROUTINE == "personal_routine"
        assert NodeType.POLICY == "policy"
        assert NodeType.KNOWLEDGE_ARTICLE == "knowledge_article"

    def test_all_statuses_exist(self):
        assert NodeStatus.DRAFT == "draft"
        assert NodeStatus.ACTIVE == "active"
        assert NodeStatus.UNDER_REVIEW == "under_review"
        assert NodeStatus.DEPRECATED == "deprecated"
        assert NodeStatus.ARCHIVED == "archived"


class TestCivilizationNode:
    def test_default_instantiation(self):
        node = CivilizationNode(
            org_id="org_123",
            node_type=NodeType.KNOWLEDGE_ARTICLE,
            title="Test Article",
        )
        assert node.id is not None
        assert isinstance(node.id, UUID)
        assert node.org_id == "org_123"
        assert node.node_type == NodeType.KNOWLEDGE_ARTICLE
        assert node.title == "Test Article"
        assert node.status == NodeStatus.ACTIVE
        assert node.version == "1.0.0"
        assert node.tags == []
        assert isinstance(node.created_at, datetime)
        assert isinstance(node.updated_at, datetime)

    def test_node_id_is_unique(self):
        n1 = CivilizationNode(org_id="o", node_type=NodeType.ROLE, title="A")
        n2 = CivilizationNode(org_id="o", node_type=NodeType.ROLE, title="B")
        assert n1.id != n2.id

    def test_custom_fields(self):
        node = CivilizationNode(
            org_id="org_456",
            node_type=NodeType.SOP,
            title="My SOP",
            status=NodeStatus.DRAFT,
            version="2.1.0",
            owner_id="user_123",
            tags=["urgent", "hr"],
            source="notion",
            source_url="https://notion.so/page/123",
            metadata={"custom": "data"},
        )
        assert node.status == NodeStatus.DRAFT
        assert node.version == "2.1.0"
        assert node.owner_id == "user_123"
        assert "urgent" in node.tags
        assert node.source == "notion"
        assert node.metadata["custom"] == "data"


class TestSOP:
    def test_sop_creation(self):
        step = SOPStep(
            step_number=1,
            title="First Step",
            description="Do this first",
            responsible_role="Manager",
            tools_required=["Jira", "Slack"],
            estimated_duration_minutes=15,
        )
        sop = SOP(
            org_id="org_1",
            title="Onboarding SOP",
            purpose="Onboard new employees",
            scope="All departments",
            triggers=["New hire starts"],
            steps=[step],
            roles_involved=["Manager", "HR"],
            tools_required=["Jira"],
            estimated_total_minutes=60,
            success_criteria=["Employee has access to all systems"],
        )
        assert sop.node_type == NodeType.SOP
        assert sop.purpose == "Onboard new employees"
        assert len(sop.steps) == 1
        assert sop.steps[0].step_number == 1
        assert "Manager" in sop.roles_involved

    def test_sop_step_substeps(self):
        substep = SOPStep(step_number=1, title="Substep", description="Detail")
        step = SOPStep(
            step_number=1,
            title="Main Step",
            description="Main",
            substeps=[substep],
        )
        assert len(step.substeps) == 1
        assert step.substeps[0].title == "Substep"


class TestJobDescription:
    def test_job_description_creation(self):
        jd = JobDescription(
            org_id="org_1",
            title="Software Engineer JD",
            role_title="Software Engineer",
            department="Engineering",
            reports_to="Engineering Manager",
            summary="Build amazing software",
            responsibilities=["Write code", "Review PRs"],
            required_skills=["Python", "Go"],
            preferred_skills=["Rust"],
            required_experience_years=3,
            tools_used=["Git", "Docker"],
        )
        assert jd.node_type == NodeType.JOB_DESCRIPTION
        assert jd.role_title == "Software Engineer"
        assert "Python" in jd.required_skills
        assert jd.required_experience_years == 3


class TestRole:
    def test_role_creation(self):
        role = Role(
            org_id="org_1",
            title="Product Manager Role",
            role_title="Product Manager",
            department="Product",
            accountabilities=["Product roadmap", "Stakeholder alignment"],
            responsibilities=["Define features", "Prioritize backlog"],
            decision_authority=["Feature scope", "Sprint priorities"],
            escalation_path=["Director of Product", "VP Engineering"],
            interfaces=["Engineering", "Design", "Sales"],
        )
        assert role.node_type == NodeType.ROLE
        assert role.role_title == "Product Manager"
        assert len(role.accountabilities) == 2


class TestPerson:
    def test_person_creation(self):
        person = Person(
            org_id="org_1",
            title="John Doe",
            name="John Doe",
            email="john@example.com",
            department="Engineering",
            location="San Francisco",
            timezone="America/Los_Angeles",
            expertise_areas=["Python", "Machine Learning"],
        )
        assert person.node_type == NodeType.PERSON
        assert person.name == "John Doe"
        assert person.email == "john@example.com"


class TestChecklist:
    def test_checklist_creation(self):
        item = ChecklistItem(
            item_number=1,
            description="Verify email access",
            required=True,
            verification_method="Send test email",
            time_estimate_minutes=5,
        )
        checklist = Checklist(
            org_id="org_1",
            title="New Employee Checklist",
            purpose="Ensure new hires have everything",
            frequency="per-event",
            items=[item],
            roles_involved=["HR", "IT"],
        )
        assert checklist.node_type == NodeType.CHECKLIST
        assert len(checklist.items) == 1
        assert checklist.items[0].required is True


class TestRunbook:
    def test_runbook_creation(self):
        step = RunbookStep(
            step_number=1,
            action="Check service status",
            command="kubectl get pods",
            expected_output="All pods Running",
            on_failure="Escalate to on-call",
        )
        runbook = Runbook(
            org_id="org_1",
            title="API Service Recovery",
            system="api-gateway",
            scenario="API returns 5xx errors",
            severity="P1",
            prerequisites=["VPN access", "kubectl configured"],
            steps=[step],
            escalation_contacts=["oncall@example.com"],
        )
        assert runbook.node_type == NodeType.RUNBOOK
        assert runbook.system == "api-gateway"
        assert runbook.severity == "P1"


class TestWorkflow:
    def test_workflow_creation(self):
        nodes = [
            WorkflowNode(node_id="start", label="Start", node_type="start"),
            WorkflowNode(node_id="review", label="Review Request", node_type="task"),
            WorkflowNode(node_id="end", label="End", node_type="end"),
        ]
        edges = [
            WorkflowEdge(from_node="start", to_node="review"),
            WorkflowEdge(from_node="review", to_node="end", condition="approved"),
        ]
        workflow = Workflow(
            org_id="org_1",
            title="Expense Approval",
            description="Process expense reimbursements",
            department="Finance",
            nodes=nodes,
            edges=edges,
            sla_hours=48,
        )
        assert workflow.node_type == NodeType.WORKFLOW
        assert len(workflow.nodes) == 3
        assert len(workflow.edges) == 2


class TestOrgChart:
    def test_org_chart_creation(self):
        units = [
            OrgUnit(
                unit_id="eng",
                name="Engineering",
                unit_type="department",
                mission="Build great products",
            ),
            OrgUnit(
                unit_id="backend",
                name="Backend Team",
                unit_type="team",
                parent_unit_id="eng",
            ),
        ]
        org_chart = OrgChart(
            org_id="org_1",
            title="Acme Corp Org Chart",
            organization_name="Acme Corporation",
            units=units,
            total_headcount=150,
        )
        assert org_chart.node_type == NodeType.ORG_CHART
        assert len(org_chart.units) == 2
        assert org_chart.total_headcount == 150


class TestClientProfile:
    def test_client_profile_creation(self):
        profile = ClientProfile(
            org_id="org_1",
            title="BigCorp Profile",
            client_name="BigCorp Inc",
            industry="Finance",
            tier="Enterprise",
            contract_value_usd=500000,
            health_score=0.85,
            goals=["Reduce costs", "Improve efficiency"],
            pain_points=["Legacy systems", "Slow deployment"],
        )
        assert profile.node_type == NodeType.CLIENT_PROFILE
        assert profile.client_name == "BigCorp Inc"
        assert profile.health_score == 0.85

    def test_health_score_validation(self):
        with pytest.raises(ValueError):
            ClientProfile(
                org_id="o",
                title="Test",
                client_name="Test",
                health_score=1.5,  # Invalid
            )


class TestPersonalRoutine:
    def test_personal_routine_creation(self):
        block = RoutineBlock(
            time="09:00",
            duration_minutes=60,
            activity="Deep work",
            category="focus",
            priority="high",
        )
        routine = PersonalRoutine(
            org_id="org_1",
            title="Morning Routine",
            person_id=uuid4(),
            routine_type="morning",
            day_of_week=["monday", "tuesday", "wednesday"],
            blocks=[block],
            goals=["Stay focused", "Avoid meetings"],
            non_negotiables=["No meetings before 10am"],
        )
        assert routine.node_type == NodeType.PERSONAL_ROUTINE
        assert len(routine.blocks) == 1
        assert routine.blocks[0].time == "09:00"

    def test_routine_block_time_validation(self):
        with pytest.raises(ValueError):
            RoutineBlock(
                time="9:00",  # Invalid format (should be HH:MM)
                duration_minutes=60,
                activity="Work",
                category="focus",
            )
