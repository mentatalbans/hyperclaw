"""
Unit tests for civilization/versioning/ — version manager, diff engine, staleness.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from civilization.schema import NodeType, NodeStatus, SOP, SOPStep, CivilizationNode
from civilization.versioning.version_manager import VersionManager, NodeVersion, VersionHistory
from civilization.versioning.diff_engine import DiffEngine, DiffType
from civilization.versioning.staleness_detector import StalenessDetector, StalenessLevel


class TestVersionManager:
    @pytest.fixture
    def manager(self):
        return VersionManager(org_id="org_test")

    @pytest.fixture
    def sample_sop(self):
        return SOP(
            org_id="org_test",
            title="Test SOP",
            purpose="Purpose",
            scope="Scope",
            steps=[SOPStep(step_number=1, title="Step", description="Desc")],
        )

    def test_create_initial_version(self, manager, sample_sop):
        version = manager.create_version(
            sample_sop,
            change_summary="Initial creation",
        )
        assert isinstance(version, NodeVersion)
        assert version.version_number == "1.0.0"
        assert version.node_id == sample_sop.id
        assert version.change_summary == "Initial creation"

    def test_version_increment_patch(self, manager, sample_sop):
        manager.create_version(sample_sop)  # 1.0.0
        version2 = manager.create_version(sample_sop, bump_type="patch")
        assert version2.version_number == "1.0.1"

    def test_version_increment_minor(self, manager, sample_sop):
        manager.create_version(sample_sop)  # 1.0.0
        version2 = manager.create_version(sample_sop, bump_type="minor")
        assert version2.version_number == "1.1.0"

    def test_version_increment_major(self, manager, sample_sop):
        manager.create_version(sample_sop)  # 1.0.0
        version2 = manager.create_version(sample_sop, bump_type="major")
        assert version2.version_number == "2.0.0"

    def test_get_history(self, manager, sample_sop):
        manager.create_version(sample_sop)
        manager.create_version(sample_sop)
        manager.create_version(sample_sop)

        history = manager.get_history(sample_sop.id)
        assert isinstance(history, VersionHistory)
        assert history.version_count == 3

    def test_get_version(self, manager, sample_sop):
        manager.create_version(sample_sop)  # 1.0.0
        manager.create_version(sample_sop)  # 1.0.1

        version = manager.get_version(sample_sop.id, "1.0.0")
        assert version is not None
        assert version.version_number == "1.0.0"

    def test_has_changes(self, manager, sample_sop):
        # New node has changes
        assert manager.has_changes(sample_sop) is True

        manager.create_version(sample_sop)

        # After versioning, no changes
        assert manager.has_changes(sample_sop) is False

        # Modify node
        sample_sop.title = "Modified Title"
        assert manager.has_changes(sample_sop) is True

    def test_restore_version(self, manager, sample_sop):
        manager.create_version(sample_sop)
        original_title = sample_sop.title

        sample_sop.title = "New Title"
        manager.create_version(sample_sop)

        restored = manager.restore_version(sample_sop.id, "1.0.0")
        assert restored is not None
        assert restored.title == original_title

    def test_compare_versions(self, manager, sample_sop):
        manager.create_version(sample_sop)  # 1.0.0

        sample_sop.title = "Updated Title"
        manager.create_version(sample_sop)  # 1.0.1

        changes = manager.compare_versions(sample_sop.id, "1.0.0", "1.0.1")
        assert changes is not None
        assert "title" in changes["changed"]

    def test_get_version_timeline(self, manager, sample_sop):
        manager.create_version(sample_sop, change_summary="First")
        manager.create_version(sample_sop, change_summary="Second")

        timeline = manager.get_version_timeline(sample_sop.id)
        assert len(timeline) == 2
        assert timeline[0]["version"] == "1.0.0"
        assert timeline[0]["change_summary"] == "First"

    def test_cleanup_old_versions(self, manager, sample_sop):
        for _ in range(15):
            manager.create_version(sample_sop)

        removed = manager.cleanup_old_versions(sample_sop.id, keep_count=5)
        assert removed == 10

        history = manager.get_history(sample_sop.id)
        assert history.version_count == 5


class TestDiffEngine:
    @pytest.fixture
    def engine(self):
        return DiffEngine()

    def test_diff_unchanged_nodes(self, engine):
        sop = SOP(
            org_id="o",
            title="Test",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        diff = engine.diff_nodes(sop, sop)
        assert diff.diff_type == DiffType.UNCHANGED

    def test_diff_field_changes(self, engine):
        sop1 = SOP(
            org_id="o",
            title="Old Title",
            purpose="Old Purpose",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        sop2 = SOP(
            id=sop1.id,
            org_id="o",
            title="New Title",
            purpose="New Purpose",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        diff = engine.diff_nodes(sop1, sop2)
        assert diff.diff_type == DiffType.CHANGED
        assert len(diff.field_diffs) >= 2

        title_diff = next(f for f in diff.field_diffs if f.field_name == "title")
        assert title_diff.old_value == "Old Title"
        assert title_diff.new_value == "New Title"

    def test_diff_step_added(self, engine):
        sop1 = SOP(
            org_id="o",
            title="T",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="Step 1", description="D1")],
        )
        sop2 = SOP(
            id=sop1.id,
            org_id="o",
            title="T",
            purpose="P",
            scope="S",
            steps=[
                SOPStep(step_number=1, title="Step 1", description="D1"),
                SOPStep(step_number=2, title="Step 2", description="D2"),
            ],
        )
        diff = engine.diff_nodes(sop1, sop2)
        added_steps = [s for s in diff.step_diffs if s.diff_type == DiffType.ADDED]
        assert len(added_steps) == 1
        assert added_steps[0].step_number == 2

    def test_diff_step_removed(self, engine):
        sop1 = SOP(
            org_id="o",
            title="T",
            purpose="P",
            scope="S",
            steps=[
                SOPStep(step_number=1, title="Step 1", description="D1"),
                SOPStep(step_number=2, title="Step 2", description="D2"),
            ],
        )
        sop2 = SOP(
            id=sop1.id,
            org_id="o",
            title="T",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="Step 1", description="D1")],
        )
        diff = engine.diff_nodes(sop1, sop2)
        removed_steps = [s for s in diff.step_diffs if s.diff_type == DiffType.REMOVED]
        assert len(removed_steps) == 1

    def test_diff_summary(self, engine):
        sop1 = SOP(
            org_id="o",
            title="Old",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        sop2 = SOP(
            id=sop1.id,
            org_id="o",
            title="New",
            purpose="P",
            scope="S",
            steps=[
                SOPStep(step_number=1, title="T", description="D"),
                SOPStep(step_number=2, title="T2", description="D2"),
            ],
        )
        diff = engine.diff_nodes(sop1, sop2)
        assert diff.summary != ""
        assert "Changed" in diff.summary or "added" in diff.summary

    def test_text_diff(self, engine):
        old = "Line 1\nLine 2\nLine 3"
        new = "Line 1\nLine 2 modified\nLine 3"
        diff_text = engine.text_diff(old, new)
        assert "-Line 2" in diff_text or "+Line 2 modified" in diff_text


class TestStalenessDetector:
    @pytest.fixture
    def detector(self):
        return StalenessDetector(org_id="org_test")

    @pytest.fixture
    def fresh_sop(self):
        return SOP(
            org_id="o",
            title="Fresh SOP",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )  # updated_at defaults to now

    @pytest.fixture
    def old_sop(self):
        sop = SOP(
            org_id="o",
            title="Old SOP",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        sop.updated_at = datetime.utcnow() - timedelta(days=400)
        return sop

    def test_check_fresh_node(self, detector, fresh_sop):
        result = detector.check_node(fresh_sop)
        assert result.level == StalenessLevel.FRESH
        assert result.days_since_update < 30

    def test_check_stale_node(self, detector, old_sop):
        result = detector.check_node(old_sop)
        assert result.level in {StalenessLevel.STALE, StalenessLevel.CRITICAL}
        assert result.days_since_update > 300

    def test_check_node_recommended_action(self, detector, old_sop):
        result = detector.check_node(old_sop)
        assert result.recommended_action is not None
        assert "review" in result.recommended_action.lower() or "critical" in result.recommended_action.lower()

    def test_check_nodes_report(self, detector, fresh_sop, old_sop):
        report = detector.check_nodes([fresh_sop, old_sop])
        assert report.stale_count >= 1
        assert report.freshness_score > 0
        assert len(report.results) == 2

    def test_report_sorted_by_priority(self, detector, fresh_sop, old_sop):
        report = detector.check_nodes([fresh_sop, old_sop])
        # Old SOP should have higher priority (come first)
        assert report.results[0].priority_score >= report.results[1].priority_score

    def test_get_review_schedule(self, detector):
        # Create a node that will become stale in 20 days
        sop = SOP(
            org_id="o",
            title="Almost Stale",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        # Set updated_at to 350 days ago (stale threshold is 365)
        sop.updated_at = datetime.utcnow() - timedelta(days=350)

        schedule = detector.get_review_schedule([sop], lookahead_days=30)
        assert len(schedule) == 1
        assert schedule[0]["days_until_stale"] < 30

    def test_get_health_metrics(self, detector, fresh_sop, old_sop):
        metrics = detector.get_health_metrics([fresh_sop, old_sop])
        assert "freshness_score" in metrics
        assert "total_nodes" in metrics
        assert "stale_nodes" in metrics
        assert metrics["total_nodes"] == 2

    def test_custom_thresholds(self):
        custom = StalenessDetector(
            org_id="o",
            custom_thresholds={
                NodeType.SOP: {"fresh": 7, "current": 14, "aging": 30, "stale": 60}
            },
        )
        sop = SOP(
            org_id="o",
            title="Test",
            purpose="P",
            scope="S",
            steps=[SOPStep(step_number=1, title="T", description="D")],
        )
        sop.updated_at = datetime.utcnow() - timedelta(days=10)

        result = custom.check_node(sop)
        # With custom threshold, 10 days is "current" not "fresh"
        assert result.level == StalenessLevel.CURRENT
