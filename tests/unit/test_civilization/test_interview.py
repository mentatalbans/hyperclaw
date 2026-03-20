"""
Unit tests for civilization/interview/ — interviewer, gap detector, questioner.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import NodeType, NodeStatus, SOP, SOPStep, Role, Checklist, ChecklistItem, Runbook, RunbookStep
from civilization.interview.interviewer import Interviewer, InterviewSession, InterviewState, InterviewTemplate
from civilization.interview.gap_detector import GapDetector, GapSeverity, GapType, KnowledgeGap
from civilization.interview.questioner import Questioner, Question, QuestionType, QuestionContext


class TestInterviewTemplate:
    def test_sop_template_exists(self):
        template = InterviewTemplate.for_sop()
        assert template.node_type == NodeType.SOP
        assert len(template.questions) > 0

    def test_role_template_exists(self):
        template = InterviewTemplate.for_role()
        assert template.node_type == NodeType.ROLE
        assert len(template.questions) > 0

    def test_checklist_template_exists(self):
        template = InterviewTemplate.for_checklist()
        assert template.node_type == NodeType.CHECKLIST

    def test_get_template_factory(self):
        for node_type in [NodeType.SOP, NodeType.ROLE, NodeType.CHECKLIST]:
            template = InterviewTemplate.get_template(node_type)
            assert template is not None


class TestInterviewer:
    @pytest.fixture
    def interviewer(self):
        return Interviewer(org_id="org_test")

    def test_start_session(self, interviewer):
        session = interviewer.start_session(
            target_node_type=NodeType.SOP,
            subject="Customer Onboarding",
            interviewee_id="user_123",
        )
        assert isinstance(session, InterviewSession)
        assert session.state == InterviewState.IN_PROGRESS
        assert session.target_node_type == NodeType.SOP
        assert session.subject == "Customer Onboarding"
        assert session.total_questions > 0
        assert session.started_at is not None

    def test_get_current_question(self, interviewer):
        session = interviewer.start_session(NodeType.SOP, "Test SOP")
        question = interviewer.get_current_question(session.id)
        assert question is not None
        assert "id" in question
        assert "text" in question

    def test_submit_response_advances(self, interviewer):
        session = interviewer.start_session(NodeType.SOP, "Test SOP")
        initial_index = session.current_question_index

        result = interviewer.submit_response(session.id, "My answer")
        assert result["is_complete"] is False or result["is_complete"] is True
        assert session.current_question_index == initial_index + 1
        assert len(session.responses) == 1

    def test_session_progress(self, interviewer):
        session = interviewer.start_session(NodeType.SOP, "Test")
        assert session.progress == 0.0

        interviewer.submit_response(session.id, "answer 1")
        assert session.progress > 0.0

    def test_pause_and_resume(self, interviewer):
        session = interviewer.start_session(NodeType.SOP, "Test")
        assert interviewer.pause_session(session.id) is True
        assert session.state == InterviewState.PAUSED

        resumed = interviewer.resume_session(session.id)
        assert resumed is not None
        assert resumed.state == InterviewState.IN_PROGRESS

    def test_abandon_session(self, interviewer):
        session = interviewer.start_session(NodeType.SOP, "Test")
        assert interviewer.abandon_session(session.id) is True
        assert session.state == InterviewState.ABANDONED

    def test_complete_interview(self, interviewer):
        session = interviewer.start_session(NodeType.SOP, "Test SOP")

        # Answer all questions
        while session.state == InterviewState.IN_PROGRESS:
            result = interviewer.submit_response(session.id, "Test answer")
            if result.get("is_complete"):
                break

        assert session.state == InterviewState.COMPLETED
        assert session.completed_at is not None


class TestGapDetector:
    @pytest.fixture
    def detector(self):
        return GapDetector(org_id="org_test")

    @pytest.fixture
    def sample_nodes(self):
        return [
            SOP(
                org_id="org_test",
                title="Incomplete SOP",
                purpose="",  # Missing purpose
                scope="",
                steps=[SOPStep(step_number=1, title="Step", description="Only one step")],
            ),
            Role(
                org_id="org_test",
                title="Sparse Role",
                role_title="Manager",
                department="Eng",
                accountabilities=[],  # Missing
                responsibilities=["One thing"],  # Too few
                decision_authority=[],
                escalation_path=[],
                interfaces=[],
            ),
        ]

    @pytest.mark.asyncio
    async def test_analyze_detects_gaps(self, detector, sample_nodes):
        report = await detector.analyze(sample_nodes)
        assert len(report.gaps) > 0
        assert report.coverage_score is not None
        assert report.health_score is not None

    @pytest.mark.asyncio
    async def test_incomplete_node_detection(self, detector, sample_nodes):
        report = await detector.analyze(sample_nodes)
        incomplete_gaps = report.by_type(GapType.INCOMPLETE_NODE)
        assert len(incomplete_gaps) > 0

    @pytest.mark.asyncio
    async def test_stale_content_detection(self, detector):
        old_node = SOP(
            org_id="org_test",
            title="Old SOP",
            purpose="Old stuff",
            scope="All",
            steps=[SOPStep(step_number=1, title="S", description="D")],
        )
        # Make it old
        old_node.updated_at = datetime.utcnow() - timedelta(days=200)

        report = await detector.analyze([old_node])
        stale_gaps = [g for g in report.gaps if g.gap_type == GapType.STALE_CONTENT]
        assert len(stale_gaps) > 0

    @pytest.mark.asyncio
    async def test_missing_owner_detection(self, detector):
        node = SOP(
            org_id="org_test",
            title="Unowned SOP",
            purpose="Purpose",
            scope="Scope",
            steps=[SOPStep(step_number=1, title="S", description="D")],
            owner_id=None,
        )
        report = await detector.analyze([node])
        owner_gaps = [g for g in report.gaps if g.gap_type == GapType.MISSING_OWNER]
        assert len(owner_gaps) > 0

    @pytest.mark.asyncio
    async def test_report_recommendations(self, detector, sample_nodes):
        report = await detector.analyze(sample_nodes)
        assert len(report.recommendations) > 0


class TestQuestioner:
    @pytest.fixture
    def questioner(self):
        return Questioner()

    def test_get_initial_questions_sop(self, questioner):
        context = QuestionContext(
            target_node_type=NodeType.SOP,
            subject="Customer Onboarding",
        )
        questions = questioner.get_initial_questions(context)
        assert len(questions) > 0
        assert all(isinstance(q, Question) for q in questions)

    def test_initial_questions_depth_brief(self, questioner):
        context = QuestionContext(
            target_node_type=NodeType.SOP,
            subject="Test",
            interview_depth="brief",
        )
        brief_qs = questioner.get_initial_questions(context)

        context.interview_depth = "thorough"
        thorough_qs = questioner.get_initial_questions(context)

        assert len(brief_qs) < len(thorough_qs)

    def test_generate_follow_up_for_vague_answer(self, questioner):
        context = QuestionContext(
            target_node_type=NodeType.SOP,
            subject="Test",
        )
        follow_ups = questioner.generate_follow_up(
            "q1",
            "Sometimes we do this, it depends on the situation",
            context,
        )
        assert len(follow_ups) > 0
        assert any(q.question_type == QuestionType.CLARIFICATION for q in follow_ups)

    def test_gap_questions(self, questioner):
        context = QuestionContext(
            target_node_type=NodeType.SOP,
            subject="Test",
        )
        gaps = ["missing_owner", "missing_tools"]
        questions = questioner.generate_gap_questions(gaps, context)
        assert len(questions) >= 2

    def test_format_question_for_display(self, questioner):
        question = Question(
            id="test",
            text="What is the purpose?",
            context="We need to understand the why",
            examples=["Reduce costs", "Improve efficiency"],
        )
        formatted = questioner.format_question_for_display(question)
        assert "What is the purpose?" in formatted
        assert "Context:" in formatted
        assert "Examples:" in formatted
