"""
Pydantic v2 models for all Civilization Knowledge Layer node types.
Every node has: id, org_id, version, created_at, updated_at, embedding.
"""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from uuid import UUID, uuid4
from datetime import datetime
from typing import Any


class NodeType(str, Enum):
    SOP = "sop"
    JOB_DESCRIPTION = "job_description"
    CHECKLIST = "checklist"
    RUNBOOK = "runbook"
    WORKFLOW = "workflow"
    ORG_CHART = "org_chart"
    ROLE = "role"
    PERSON = "person"
    CLIENT_PROFILE = "client_profile"
    PERSONAL_ROUTINE = "personal_routine"
    POLICY = "policy"
    KNOWLEDGE_ARTICLE = "knowledge_article"


class NodeStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class CivilizationNode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    org_id: str
    node_type: NodeType
    title: str
    status: NodeStatus = NodeStatus.ACTIVE
    version: str = "1.0.0"
    owner_id: str | None = None
    tags: list[str] = []
    source: str | None = None
    source_url: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SOPStep(BaseModel):
    step_number: int
    title: str
    description: str
    responsible_role: str | None = None
    tools_required: list[str] = []
    estimated_duration_minutes: int | None = None
    decision_points: list[str] = []
    on_failure: str | None = None
    substeps: list["SOPStep"] = []


class SOP(CivilizationNode):
    node_type: NodeType = NodeType.SOP
    purpose: str
    scope: str
    triggers: list[str] = []
    steps: list[SOPStep]
    roles_involved: list[str] = []
    tools_required: list[str] = []
    estimated_total_minutes: int | None = None
    success_criteria: list[str] = []
    related_sops: list[UUID] = []
    kpis: list[str] = []


class JobDescription(CivilizationNode):
    node_type: NodeType = NodeType.JOB_DESCRIPTION
    role_title: str
    department: str
    reports_to: str | None = None
    direct_reports: list[str] = []
    level: str | None = None
    employment_type: str = "full_time"
    summary: str
    responsibilities: list[str]
    required_skills: list[str]
    preferred_skills: list[str] = []
    required_experience_years: int | None = None
    tools_used: list[str] = []
    kpis: list[str] = []
    okrs: list[str] = []
    success_metrics: list[str] = []
    compensation_band: str | None = None
    related_roles: list[UUID] = []


class Role(CivilizationNode):
    node_type: NodeType = NodeType.ROLE
    role_title: str
    department: str
    accountabilities: list[str]
    responsibilities: list[str]
    decision_authority: list[str]
    escalation_path: list[str]
    interfaces: list[str]
    tools: list[str] = []
    sops_owned: list[UUID] = []
    checklists_owned: list[UUID] = []


class Person(CivilizationNode):
    node_type: NodeType = NodeType.PERSON
    name: str
    email: str | None = None
    role_id: UUID | None = None
    manager_id: UUID | None = None
    direct_report_ids: list[UUID] = []
    department: str | None = None
    location: str | None = None
    timezone: str | None = None
    communication_preferences: dict = {}
    expertise_areas: list[str] = []
    current_projects: list[str] = []
    personal_routine_id: UUID | None = None


class ChecklistItem(BaseModel):
    item_number: int
    description: str
    required: bool = True
    verification_method: str | None = None
    responsible_role: str | None = None
    time_estimate_minutes: int | None = None
    notes: str | None = None


class Checklist(CivilizationNode):
    node_type: NodeType = NodeType.CHECKLIST
    purpose: str
    frequency: str | None = None
    trigger: str | None = None
    items: list[ChecklistItem]
    roles_involved: list[str] = []
    estimated_total_minutes: int | None = None
    related_sop_id: UUID | None = None


class RunbookStep(BaseModel):
    step_number: int
    action: str
    command: str | None = None
    expected_output: str | None = None
    on_success: str | None = None
    on_failure: str | None = None
    rollback: str | None = None


class Runbook(CivilizationNode):
    node_type: NodeType = NodeType.RUNBOOK
    system: str
    scenario: str
    severity: str | None = None
    prerequisites: list[str] = []
    steps: list[RunbookStep]
    rollback_procedure: list[str] = []
    escalation_contacts: list[str] = []
    post_incident_actions: list[str] = []


class WorkflowNode(BaseModel):
    node_id: str
    label: str
    node_type: str  # "start"|"end"|"task"|"decision"|"approval"
    responsible_role: str | None = None
    tool: str | None = None
    description: str | None = None


class WorkflowEdge(BaseModel):
    from_node: str
    to_node: str
    condition: str | None = None


class Workflow(CivilizationNode):
    node_type: NodeType = NodeType.WORKFLOW
    description: str
    department: str | None = None
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    triggers: list[str] = []
    sla_hours: int | None = None
    related_sop_id: UUID | None = None


class OrgUnit(BaseModel):
    unit_id: str
    name: str
    unit_type: str
    head_person_id: UUID | None = None
    parent_unit_id: str | None = None
    member_ids: list[UUID] = []
    mission: str | None = None
    budget_owner: bool = False


class OrgChart(CivilizationNode):
    node_type: NodeType = NodeType.ORG_CHART
    organization_name: str
    units: list[OrgUnit]
    total_headcount: int | None = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class ClientProfile(CivilizationNode):
    node_type: NodeType = NodeType.CLIENT_PROFILE
    client_name: str
    industry: str | None = None
    tier: str | None = None
    relationship_owner_id: UUID | None = None
    key_contacts: list[dict] = []
    contract_value_usd: float | None = None
    contract_start: datetime | None = None
    contract_end: datetime | None = None
    health_score: float | None = None
    goals: list[str] = []
    pain_points: list[str] = []
    preferences: dict = {}
    active_projects: list[str] = []
    communication_cadence: str | None = None
    notes: str | None = None
    related_sops: list[UUID] = []

    @field_validator("health_score")
    @classmethod
    def validate_health_score(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("health_score must be between 0.0 and 1.0")
        return v


class RoutineBlock(BaseModel):
    time: str  # "06:00" 24h format
    duration_minutes: int
    activity: str
    category: str
    priority: str = "medium"
    tools: list[str] = []
    notes: str | None = None

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v):
        import re
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("time must be in HH:MM format")
        return v


class PersonalRoutine(CivilizationNode):
    node_type: NodeType = NodeType.PERSONAL_ROUTINE
    person_id: UUID
    routine_type: str
    day_of_week: list[str] = []
    blocks: list[RoutineBlock]
    goals: list[str] = []
    non_negotiables: list[str] = []
    flexibility_rules: list[str] = []


class Policy(CivilizationNode):
    node_type: NodeType = NodeType.POLICY
    policy_name: str
    category: str
    applies_to: list[str] = []
    effective_date: datetime | None = None
    review_date: datetime | None = None
    summary: str
    full_text: str
    exceptions: list[str] = []
    related_policies: list[UUID] = []
    compliance_requirements: list[str] = []


class KnowledgeArticle(CivilizationNode):
    node_type: NodeType = NodeType.KNOWLEDGE_ARTICLE
    topic: str
    category: str
    summary: str
    content: str
    author_id: UUID | None = None
    reviewers: list[UUID] = []
    related_articles: list[UUID] = []
    related_sops: list[UUID] = []
    faq: list[dict] = []
    last_reviewed: datetime | None = None
