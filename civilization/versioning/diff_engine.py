"""
Diff engine for comparing civilization nodes.
Provides detailed structural diffs between node versions.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID
import difflib

from ..schema import CivilizationNode, SOP, SOPStep, Checklist, ChecklistItem

logger = logging.getLogger(__name__)


class DiffType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    MOVED = "moved"


@dataclass
class FieldDiff:
    """Diff for a single field."""
    field_name: str
    diff_type: DiffType
    old_value: Any = None
    new_value: Any = None
    path: str = ""  # JSON path for nested fields


@dataclass
class StepDiff:
    """Diff for a step in SOP or Checklist."""
    step_number: int
    diff_type: DiffType
    field_diffs: list[FieldDiff] = field(default_factory=list)
    old_step: dict | None = None
    new_step: dict | None = None


@dataclass
class NodeDiff:
    """Complete diff between two node versions."""
    node_id: UUID
    old_version: str
    new_version: str
    diff_type: DiffType = DiffType.CHANGED
    field_diffs: list[FieldDiff] = field(default_factory=list)
    step_diffs: list[StepDiff] = field(default_factory=list)
    summary: str = ""

    @property
    def has_changes(self) -> bool:
        return self.diff_type != DiffType.UNCHANGED

    @property
    def change_count(self) -> int:
        return len(self.field_diffs) + len(self.step_diffs)


class DiffEngine:
    """
    Computes detailed diffs between civilization nodes.
    Handles structural comparisons for SOPs, checklists, and other node types.
    """

    # Fields to ignore in diff
    IGNORE_FIELDS = {"updated_at", "embedding", "id"}

    # Fields that are considered structural (not content)
    STRUCTURAL_FIELDS = {"steps", "items", "nodes", "edges", "units"}

    def diff_nodes(
        self,
        old_node: CivilizationNode,
        new_node: CivilizationNode,
    ) -> NodeDiff:
        """
        Compute diff between two nodes.

        Args:
            old_node: The previous version
            new_node: The current version

        Returns:
            NodeDiff with detailed changes
        """
        result = NodeDiff(
            node_id=new_node.id,
            old_version=old_node.version,
            new_version=new_node.version,
        )

        old_data = old_node.model_dump(mode="json")
        new_data = new_node.model_dump(mode="json")

        # Compare top-level fields
        for field_name in set(old_data.keys()) | set(new_data.keys()):
            if field_name in self.IGNORE_FIELDS:
                continue

            old_val = old_data.get(field_name)
            new_val = new_data.get(field_name)

            if field_name in self.STRUCTURAL_FIELDS:
                # Handle structural fields separately
                if field_name == "steps":
                    result.step_diffs.extend(
                        self._diff_steps(old_val or [], new_val or [])
                    )
                elif field_name == "items":
                    result.step_diffs.extend(
                        self._diff_items(old_val or [], new_val or [])
                    )
            else:
                field_diff = self._diff_field(field_name, old_val, new_val)
                if field_diff.diff_type != DiffType.UNCHANGED:
                    result.field_diffs.append(field_diff)

        # Determine overall diff type
        if not result.field_diffs and not result.step_diffs:
            result.diff_type = DiffType.UNCHANGED
        elif not old_data:
            result.diff_type = DiffType.ADDED
        elif not new_data:
            result.diff_type = DiffType.REMOVED

        result.summary = self._generate_summary(result)
        return result

    def _diff_field(
        self,
        field_name: str,
        old_val: Any,
        new_val: Any,
        path: str = "",
    ) -> FieldDiff:
        """Compare two field values."""
        full_path = f"{path}.{field_name}" if path else field_name

        if old_val is None and new_val is not None:
            return FieldDiff(
                field_name=field_name,
                diff_type=DiffType.ADDED,
                new_value=new_val,
                path=full_path,
            )
        elif old_val is not None and new_val is None:
            return FieldDiff(
                field_name=field_name,
                diff_type=DiffType.REMOVED,
                old_value=old_val,
                path=full_path,
            )
        elif old_val != new_val:
            return FieldDiff(
                field_name=field_name,
                diff_type=DiffType.CHANGED,
                old_value=old_val,
                new_value=new_val,
                path=full_path,
            )
        else:
            return FieldDiff(
                field_name=field_name,
                diff_type=DiffType.UNCHANGED,
                path=full_path,
            )

    def _diff_steps(
        self,
        old_steps: list[dict],
        new_steps: list[dict],
    ) -> list[StepDiff]:
        """Compare SOP steps."""
        diffs = []

        # Index by step number
        old_by_num = {s.get("step_number", i): s for i, s in enumerate(old_steps)}
        new_by_num = {s.get("step_number", i): s for i, s in enumerate(new_steps)}

        all_nums = set(old_by_num.keys()) | set(new_by_num.keys())

        for num in sorted(all_nums):
            old_step = old_by_num.get(num)
            new_step = new_by_num.get(num)

            if old_step and not new_step:
                diffs.append(StepDiff(
                    step_number=num,
                    diff_type=DiffType.REMOVED,
                    old_step=old_step,
                ))
            elif new_step and not old_step:
                diffs.append(StepDiff(
                    step_number=num,
                    diff_type=DiffType.ADDED,
                    new_step=new_step,
                ))
            elif old_step != new_step:
                field_diffs = self._compare_dicts(old_step, new_step)
                diffs.append(StepDiff(
                    step_number=num,
                    diff_type=DiffType.CHANGED,
                    field_diffs=field_diffs,
                    old_step=old_step,
                    new_step=new_step,
                ))

        return diffs

    def _diff_items(
        self,
        old_items: list[dict],
        new_items: list[dict],
    ) -> list[StepDiff]:
        """Compare checklist items."""
        diffs = []

        old_by_num = {i.get("item_number", idx): i for idx, i in enumerate(old_items)}
        new_by_num = {i.get("item_number", idx): i for idx, i in enumerate(new_items)}

        all_nums = set(old_by_num.keys()) | set(new_by_num.keys())

        for num in sorted(all_nums):
            old_item = old_by_num.get(num)
            new_item = new_by_num.get(num)

            if old_item and not new_item:
                diffs.append(StepDiff(
                    step_number=num,
                    diff_type=DiffType.REMOVED,
                    old_step=old_item,
                ))
            elif new_item and not old_item:
                diffs.append(StepDiff(
                    step_number=num,
                    diff_type=DiffType.ADDED,
                    new_step=new_item,
                ))
            elif old_item != new_item:
                field_diffs = self._compare_dicts(old_item, new_item)
                diffs.append(StepDiff(
                    step_number=num,
                    diff_type=DiffType.CHANGED,
                    field_diffs=field_diffs,
                    old_step=old_item,
                    new_step=new_item,
                ))

        return diffs

    def _compare_dicts(self, old: dict, new: dict) -> list[FieldDiff]:
        """Compare two dicts and return field diffs."""
        diffs = []
        all_keys = set(old.keys()) | set(new.keys())

        for key in all_keys:
            if key in self.IGNORE_FIELDS:
                continue
            diff = self._diff_field(key, old.get(key), new.get(key))
            if diff.diff_type != DiffType.UNCHANGED:
                diffs.append(diff)

        return diffs

    def _generate_summary(self, diff: NodeDiff) -> str:
        """Generate human-readable summary of changes."""
        if diff.diff_type == DiffType.UNCHANGED:
            return "No changes"

        parts = []

        # Field changes
        changed_fields = [f.field_name for f in diff.field_diffs if f.diff_type == DiffType.CHANGED]
        added_fields = [f.field_name for f in diff.field_diffs if f.diff_type == DiffType.ADDED]
        removed_fields = [f.field_name for f in diff.field_diffs if f.diff_type == DiffType.REMOVED]

        if changed_fields:
            parts.append(f"Changed: {', '.join(changed_fields[:5])}")
        if added_fields:
            parts.append(f"Added: {', '.join(added_fields[:3])}")
        if removed_fields:
            parts.append(f"Removed: {', '.join(removed_fields[:3])}")

        # Step changes
        added_steps = sum(1 for s in diff.step_diffs if s.diff_type == DiffType.ADDED)
        removed_steps = sum(1 for s in diff.step_diffs if s.diff_type == DiffType.REMOVED)
        changed_steps = sum(1 for s in diff.step_diffs if s.diff_type == DiffType.CHANGED)

        if added_steps:
            parts.append(f"{added_steps} step(s) added")
        if removed_steps:
            parts.append(f"{removed_steps} step(s) removed")
        if changed_steps:
            parts.append(f"{changed_steps} step(s) modified")

        return "; ".join(parts) if parts else "Changes detected"

    def text_diff(
        self,
        old_text: str,
        new_text: str,
        context_lines: int = 3,
    ) -> str:
        """Generate unified diff for text content."""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="old",
            tofile="new",
            n=context_lines,
        )
        return "".join(diff)

    def to_dict(self, diff: NodeDiff) -> dict:
        """Export diff to dictionary format."""
        return {
            "node_id": str(diff.node_id),
            "old_version": diff.old_version,
            "new_version": diff.new_version,
            "diff_type": diff.diff_type.value,
            "summary": diff.summary,
            "field_diffs": [
                {
                    "field_name": f.field_name,
                    "diff_type": f.diff_type.value,
                    "old_value": f.old_value,
                    "new_value": f.new_value,
                    "path": f.path,
                }
                for f in diff.field_diffs
            ],
            "step_diffs": [
                {
                    "step_number": s.step_number,
                    "diff_type": s.diff_type.value,
                    "old_step": s.old_step,
                    "new_step": s.new_step,
                }
                for s in diff.step_diffs
            ],
        }
