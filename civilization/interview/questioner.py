"""
Dynamic question generation for knowledge elicitation.
Generates contextual follow-up questions based on responses and gaps.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import UUID, uuid4

from ..schema import NodeType, CivilizationNode

logger = logging.getLogger(__name__)


class QuestionType(str, Enum):
    OPEN = "open"  # Open-ended questions
    CLOSED = "closed"  # Yes/No questions
    MULTIPLE_CHOICE = "multiple_choice"
    SCALE = "scale"  # 1-5 or 1-10 ratings
    LIST = "list"  # Ask for a list of items
    CLARIFICATION = "clarification"  # Follow-up for unclear answers
    CONFIRMATION = "confirmation"  # Verify understanding


class QuestionPriority(str, Enum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


@dataclass
class Question:
    """Represents a question to ask during knowledge elicitation."""
    id: str
    text: str
    question_type: QuestionType = QuestionType.OPEN
    priority: QuestionPriority = QuestionPriority.REQUIRED
    context: str | None = None
    options: list[str] = field(default_factory=list)  # For multiple choice
    follow_up_for: str | None = None  # ID of question this follows up on
    validation_hint: str | None = None
    examples: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class QuestionContext:
    """Context for generating questions."""
    target_node_type: NodeType
    subject: str
    existing_responses: dict[str, str] = field(default_factory=dict)
    existing_nodes: list[CivilizationNode] = field(default_factory=list)
    detected_gaps: list[str] = field(default_factory=list)
    interview_depth: str = "standard"  # "brief", "standard", "thorough"


class Questioner:
    """
    Generates dynamic questions for knowledge elicitation.
    Adapts questions based on context, previous responses, and detected gaps.
    """

    def __init__(
        self,
        llm_callback: Callable[[str, str], str] | None = None,
    ):
        self.llm_callback = llm_callback
        self._question_templates = self._load_templates()

    def _load_templates(self) -> dict[NodeType, list[Question]]:
        """Load question templates for each node type."""
        return {
            NodeType.SOP: [
                Question(
                    id="sop_overview",
                    text="Can you give me a high-level overview of this procedure?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                    context="Start with the big picture before diving into details",
                ),
                Question(
                    id="sop_trigger",
                    text="What events or conditions trigger this procedure?",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.REQUIRED,
                    examples=["Customer complaint received", "Monthly audit due", "New employee onboarding"],
                ),
                Question(
                    id="sop_prerequisites",
                    text="What needs to be in place before starting this procedure?",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.RECOMMENDED,
                ),
                Question(
                    id="sop_first_step",
                    text="What is the very first thing someone does when starting this procedure?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="sop_decision_points",
                    text="Are there any decision points where the procedure can branch?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.RECOMMENDED,
                    context="We want to capture if-then logic in the procedure",
                ),
                Question(
                    id="sop_handoffs",
                    text="Are there any handoffs between people or teams during this procedure?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.RECOMMENDED,
                ),
                Question(
                    id="sop_failure_modes",
                    text="What are the most common ways this procedure can fail?",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.RECOMMENDED,
                ),
                Question(
                    id="sop_time_critical",
                    text="Are there any time-sensitive steps or SLAs to be aware of?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.OPTIONAL,
                ),
            ],
            NodeType.ROLE: [
                Question(
                    id="role_mission",
                    text="What is the primary mission or purpose of this role?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="role_daily",
                    text="What does a typical day look like for someone in this role?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="role_decisions",
                    text="What decisions can this role make independently?",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="role_escalation",
                    text="What situations require escalation? To whom?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="role_interfaces",
                    text="Which other roles or teams does this role interact with most?",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.RECOMMENDED,
                ),
                Question(
                    id="role_success",
                    text="How is success measured for this role?",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.RECOMMENDED,
                ),
            ],
            NodeType.CHECKLIST: [
                Question(
                    id="cl_purpose",
                    text="What is the purpose of this checklist?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="cl_when",
                    text="When should this checklist be used?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="cl_items",
                    text="Walk me through each item on the checklist.",
                    question_type=QuestionType.LIST,
                    priority=QuestionPriority.REQUIRED,
                ),
                Question(
                    id="cl_critical",
                    text="Are any items especially critical? What happens if they're missed?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.RECOMMENDED,
                ),
            ],
        }

    def get_initial_questions(
        self,
        context: QuestionContext,
    ) -> list[Question]:
        """Get initial questions based on context."""
        templates = self._question_templates.get(context.target_node_type, [])

        # Filter by depth
        if context.interview_depth == "brief":
            return [q for q in templates if q.priority == QuestionPriority.REQUIRED]
        elif context.interview_depth == "thorough":
            return templates
        else:  # standard
            return [q for q in templates if q.priority != QuestionPriority.OPTIONAL]

    def generate_follow_up(
        self,
        question_id: str,
        response: str,
        context: QuestionContext,
    ) -> list[Question]:
        """Generate follow-up questions based on a response."""
        follow_ups = []

        # Check for vague responses that need clarification
        vague_indicators = ["sometimes", "usually", "it depends", "maybe", "i think"]
        if any(indicator in response.lower() for indicator in vague_indicators):
            follow_ups.append(Question(
                id=f"{question_id}_clarify",
                text="Can you be more specific? In what situations does this vary?",
                question_type=QuestionType.CLARIFICATION,
                priority=QuestionPriority.REQUIRED,
                follow_up_for=question_id,
            ))

        # Check for mentions of other processes/roles that aren't documented
        if context.existing_nodes:
            existing_titles = {n.title.lower() for n in context.existing_nodes}
            # Simple heuristic: look for capitalized phrases that might be references
            import re
            potential_refs = re.findall(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", response)
            for ref in potential_refs:
                if ref.lower() not in existing_titles and len(ref) > 3:
                    follow_ups.append(Question(
                        id=f"ref_{ref.lower().replace(' ', '_')}",
                        text=f"You mentioned '{ref}'. Is this a process, role, or system we should document?",
                        question_type=QuestionType.CLOSED,
                        priority=QuestionPriority.OPTIONAL,
                        follow_up_for=question_id,
                    ))
                    break  # Only ask about one reference at a time

        # Check for step mentions that need elaboration
        if "step" in response.lower() or any(c.isdigit() for c in response):
            if len(response) < 100:  # Short response about steps
                follow_ups.append(Question(
                    id=f"{question_id}_elaborate_steps",
                    text="Can you walk me through these steps in more detail?",
                    question_type=QuestionType.OPEN,
                    priority=QuestionPriority.RECOMMENDED,
                    follow_up_for=question_id,
                ))

        return follow_ups

    def generate_gap_questions(
        self,
        gaps: list[str],
        context: QuestionContext,
    ) -> list[Question]:
        """Generate questions to fill identified knowledge gaps."""
        questions = []

        gap_question_map = {
            "missing_owner": Question(
                id="gap_owner",
                text="Who is responsible for maintaining and updating this?",
                question_type=QuestionType.OPEN,
                priority=QuestionPriority.REQUIRED,
            ),
            "missing_tools": Question(
                id="gap_tools",
                text="What tools or systems are used in this process?",
                question_type=QuestionType.LIST,
                priority=QuestionPriority.REQUIRED,
            ),
            "missing_timeline": Question(
                id="gap_timeline",
                text="How long does this typically take? Are there any SLAs?",
                question_type=QuestionType.OPEN,
                priority=QuestionPriority.RECOMMENDED,
            ),
            "missing_exceptions": Question(
                id="gap_exceptions",
                text="Are there any exceptions or special cases to be aware of?",
                question_type=QuestionType.OPEN,
                priority=QuestionPriority.RECOMMENDED,
            ),
            "missing_metrics": Question(
                id="gap_metrics",
                text="How do you measure success or track performance?",
                question_type=QuestionType.LIST,
                priority=QuestionPriority.RECOMMENDED,
            ),
        }

        for gap in gaps:
            if gap in gap_question_map:
                questions.append(gap_question_map[gap])

        return questions

    async def generate_smart_question(
        self,
        context: QuestionContext,
        previous_qa: list[tuple[str, str]],
    ) -> Question | None:
        """
        Use LLM to generate a contextually relevant follow-up question.
        Requires llm_callback to be set.
        """
        if not self.llm_callback:
            return None

        # Build prompt for LLM
        qa_history = "\n".join([f"Q: {q}\nA: {a}" for q, a in previous_qa[-5:]])

        prompt = f"""You are helping document organizational knowledge about: {context.subject}
Document type: {context.target_node_type.value}

Previous Q&A:
{qa_history}

Based on this conversation, generate ONE follow-up question that would help complete the documentation.
The question should:
1. Fill in gaps in the information provided
2. Clarify any ambiguous points
3. Explore important details not yet covered

Return ONLY the question text, nothing else."""

        try:
            question_text = self.llm_callback("system", prompt)
            return Question(
                id=f"smart_{uuid4().hex[:8]}",
                text=question_text.strip(),
                question_type=QuestionType.OPEN,
                priority=QuestionPriority.RECOMMENDED,
                metadata={"generated": True},
            )
        except Exception as e:
            logger.warning(f"Failed to generate smart question: {e}")
            return None

    def format_question_for_display(self, question: Question) -> str:
        """Format a question for user display."""
        text = question.text

        if question.context:
            text = f"{text}\n\n(Context: {question.context})"

        if question.examples:
            examples_str = ", ".join(question.examples[:3])
            text = f"{text}\n\nExamples: {examples_str}"

        if question.options:
            options_str = "\n".join([f"  {i+1}. {opt}" for i, opt in enumerate(question.options)])
            text = f"{text}\n\nOptions:\n{options_str}"

        return text
