"""
Interactive interviewer for knowledge elicitation.
Guides users through structured interviews to capture organizational knowledge.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable
from uuid import UUID, uuid4

from ..schema import NodeType, CivilizationNode

logger = logging.getLogger(__name__)


class InterviewState(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass
class InterviewResponse:
    """A single response in an interview."""
    question_id: str
    question_text: str
    response_text: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    confidence: float | None = None
    follow_up_needed: bool = False
    extracted_entities: list[str] = field(default_factory=list)


@dataclass
class InterviewSession:
    """Represents an interview session."""
    id: UUID = field(default_factory=uuid4)
    org_id: str = ""
    target_node_type: NodeType = NodeType.SOP
    subject: str = ""
    interviewer_id: str | None = None
    interviewee_id: str | None = None
    state: InterviewState = InterviewState.NOT_STARTED
    responses: list[InterviewResponse] = field(default_factory=list)
    current_question_index: int = 0
    total_questions: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def progress(self) -> float:
        """Return progress as percentage (0-100)."""
        if self.total_questions == 0:
            return 0.0
        return (len(self.responses) / self.total_questions) * 100

    @property
    def duration_minutes(self) -> float | None:
        """Return interview duration in minutes."""
        if not self.started_at:
            return None
        end = self.completed_at or datetime.utcnow()
        return (end - self.started_at).total_seconds() / 60


class InterviewTemplate:
    """Template for a specific type of interview."""

    def __init__(self, node_type: NodeType, questions: list[dict]):
        self.node_type = node_type
        self.questions = questions

    @classmethod
    def for_sop(cls) -> "InterviewTemplate":
        """Create template for SOP interviews."""
        return cls(NodeType.SOP, [
            {"id": "sop_name", "text": "What is the name of this procedure?", "required": True},
            {"id": "sop_purpose", "text": "What is the purpose of this SOP? What problem does it solve?", "required": True},
            {"id": "sop_scope", "text": "What is the scope? What does this SOP cover and not cover?", "required": True},
            {"id": "sop_trigger", "text": "What triggers this procedure? When should someone start it?", "required": True},
            {"id": "sop_roles", "text": "Who is involved in this procedure? What roles are needed?", "required": True},
            {"id": "sop_steps", "text": "Walk me through the steps. What happens first, second, etc.?", "required": True},
            {"id": "sop_tools", "text": "What tools or systems are used in this procedure?", "required": False},
            {"id": "sop_duration", "text": "How long does this procedure typically take?", "required": False},
            {"id": "sop_failure", "text": "What happens if something goes wrong? How do you handle failures?", "required": False},
            {"id": "sop_success", "text": "How do you know the procedure was successful? What are the success criteria?", "required": True},
            {"id": "sop_exceptions", "text": "Are there any exceptions or edge cases to be aware of?", "required": False},
            {"id": "sop_related", "text": "Are there other procedures that relate to this one?", "required": False},
        ])

    @classmethod
    def for_role(cls) -> "InterviewTemplate":
        """Create template for role interviews."""
        return cls(NodeType.ROLE, [
            {"id": "role_title", "text": "What is the title of this role?", "required": True},
            {"id": "role_department", "text": "Which department or team does this role belong to?", "required": True},
            {"id": "role_mission", "text": "What is the primary mission or purpose of this role?", "required": True},
            {"id": "role_responsibilities", "text": "What are the key responsibilities of this role?", "required": True},
            {"id": "role_accountabilities", "text": "What outcomes is this role accountable for?", "required": True},
            {"id": "role_authority", "text": "What decisions can this role make independently?", "required": True},
            {"id": "role_escalation", "text": "Who does this role escalate issues to?", "required": True},
            {"id": "role_interfaces", "text": "Which other roles or teams does this role regularly interact with?", "required": True},
            {"id": "role_tools", "text": "What tools and systems does this role use daily?", "required": False},
            {"id": "role_sops", "text": "What SOPs or procedures does this role own or follow?", "required": False},
        ])

    @classmethod
    def for_checklist(cls) -> "InterviewTemplate":
        """Create template for checklist interviews."""
        return cls(NodeType.CHECKLIST, [
            {"id": "cl_name", "text": "What is this checklist called?", "required": True},
            {"id": "cl_purpose", "text": "What is the purpose of this checklist?", "required": True},
            {"id": "cl_trigger", "text": "When should this checklist be used?", "required": True},
            {"id": "cl_frequency", "text": "How often is this checklist used? (daily, weekly, per-event, etc.)", "required": False},
            {"id": "cl_items", "text": "Walk me through each item on the checklist.", "required": True},
            {"id": "cl_verification", "text": "How do you verify each item is complete?", "required": False},
            {"id": "cl_responsible", "text": "Who is responsible for completing this checklist?", "required": True},
            {"id": "cl_related_sop", "text": "Is this checklist part of a larger SOP?", "required": False},
        ])

    @classmethod
    def get_template(cls, node_type: NodeType) -> "InterviewTemplate":
        """Get the appropriate template for a node type."""
        templates = {
            NodeType.SOP: cls.for_sop,
            NodeType.ROLE: cls.for_role,
            NodeType.CHECKLIST: cls.for_checklist,
        }
        factory = templates.get(node_type, cls.for_sop)
        return factory()


class Interviewer:
    """
    Conducts structured interviews to elicit organizational knowledge.
    Supports asynchronous, multi-session interviews.
    """

    def __init__(
        self,
        org_id: str,
        store=None,  # CivilizationStore
        llm_callback: Callable[[str], str] | None = None,
    ):
        self.org_id = org_id
        self.store = store
        self.llm_callback = llm_callback
        self.sessions: dict[UUID, InterviewSession] = {}

    def start_session(
        self,
        target_node_type: NodeType,
        subject: str,
        interviewee_id: str | None = None,
    ) -> InterviewSession:
        """Start a new interview session."""
        template = InterviewTemplate.get_template(target_node_type)

        session = InterviewSession(
            org_id=self.org_id,
            target_node_type=target_node_type,
            subject=subject,
            interviewee_id=interviewee_id,
            state=InterviewState.IN_PROGRESS,
            total_questions=len(template.questions),
            started_at=datetime.utcnow(),
        )

        self.sessions[session.id] = session
        return session

    def get_current_question(self, session_id: UUID) -> dict | None:
        """Get the current question for a session."""
        session = self.sessions.get(session_id)
        if not session or session.state != InterviewState.IN_PROGRESS:
            return None

        template = InterviewTemplate.get_template(session.target_node_type)
        if session.current_question_index >= len(template.questions):
            return None

        return template.questions[session.current_question_index]

    def submit_response(
        self,
        session_id: UUID,
        response_text: str,
        confidence: float | None = None,
    ) -> dict:
        """
        Submit a response to the current question.

        Returns:
            Dict with: next_question, is_complete, follow_up
        """
        session = self.sessions.get(session_id)
        if not session or session.state != InterviewState.IN_PROGRESS:
            return {"error": "Invalid session or session not in progress"}

        template = InterviewTemplate.get_template(session.target_node_type)
        if session.current_question_index >= len(template.questions):
            return {"error": "No more questions"}

        current_q = template.questions[session.current_question_index]

        # Record response
        response = InterviewResponse(
            question_id=current_q["id"],
            question_text=current_q["text"],
            response_text=response_text,
            confidence=confidence,
        )
        session.responses.append(response)
        session.current_question_index += 1

        # Check if interview is complete
        if session.current_question_index >= len(template.questions):
            session.state = InterviewState.COMPLETED
            session.completed_at = datetime.utcnow()
            return {
                "is_complete": True,
                "next_question": None,
                "progress": 100.0,
            }

        next_q = template.questions[session.current_question_index]
        return {
            "is_complete": False,
            "next_question": next_q,
            "progress": session.progress,
        }

    def pause_session(self, session_id: UUID) -> bool:
        """Pause an interview session."""
        session = self.sessions.get(session_id)
        if session and session.state == InterviewState.IN_PROGRESS:
            session.state = InterviewState.PAUSED
            return True
        return False

    def resume_session(self, session_id: UUID) -> InterviewSession | None:
        """Resume a paused interview session."""
        session = self.sessions.get(session_id)
        if session and session.state == InterviewState.PAUSED:
            session.state = InterviewState.IN_PROGRESS
            return session
        return None

    def abandon_session(self, session_id: UUID) -> bool:
        """Abandon an interview session."""
        session = self.sessions.get(session_id)
        if session:
            session.state = InterviewState.ABANDONED
            return True
        return False

    def get_session(self, session_id: UUID) -> InterviewSession | None:
        """Get a session by ID."""
        return self.sessions.get(session_id)

    def compile_to_node(self, session_id: UUID) -> CivilizationNode | None:
        """
        Compile interview responses into a CivilizationNode.
        Only works for completed sessions.
        """
        session = self.sessions.get(session_id)
        if not session or session.state != InterviewState.COMPLETED:
            return None

        # Build response lookup
        responses = {r.question_id: r.response_text for r in session.responses}

        if session.target_node_type == NodeType.SOP:
            from ..schema import SOP, SOPStep
            return SOP(
                org_id=session.org_id,
                title=responses.get("sop_name", session.subject),
                purpose=responses.get("sop_purpose", ""),
                scope=responses.get("sop_scope", ""),
                triggers=[responses.get("sop_trigger", "")] if responses.get("sop_trigger") else [],
                steps=self._parse_steps(responses.get("sop_steps", "")),
                roles_involved=self._parse_list(responses.get("sop_roles", "")),
                tools_required=self._parse_list(responses.get("sop_tools", "")),
                success_criteria=self._parse_list(responses.get("sop_success", "")),
                metadata={"source": "interview", "session_id": str(session.id)},
            )

        # Add more node type compilations as needed
        return None

    def _parse_steps(self, text: str) -> list:
        """Parse step descriptions into SOPStep objects."""
        from ..schema import SOPStep
        if not text:
            return []

        # Simple parsing: split by numbered items or newlines
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        steps = []
        for i, line in enumerate(lines, 1):
            # Remove leading numbers/bullets
            import re
            cleaned = re.sub(r"^[\d\.\)\-\*]+\s*", "", line)
            if cleaned:
                steps.append(SOPStep(
                    step_number=i,
                    title=f"Step {i}",
                    description=cleaned,
                ))
        return steps if steps else [SOPStep(step_number=1, title="Step 1", description=text)]

    def _parse_list(self, text: str) -> list[str]:
        """Parse comma or newline separated list."""
        if not text:
            return []
        import re
        items = re.split(r"[,\n]", text)
        return [item.strip() for item in items if item.strip()]
