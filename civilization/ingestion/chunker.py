"""
Procedural-aware chunker. Preserves step order and injects surrounding
context into each chunk so agents can retrieve step N without losing
the fact that step N-1 must happen first.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID
from typing import Any

from ..schema import SOP, SOPStep, Checklist, ChecklistItem, Runbook, RunbookStep, CivilizationNode


@dataclass
class SOPChunk:
    sop_id: UUID
    sop_title: str
    step_number: int
    step_title: str
    content: str  # Enriched with context
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ChecklistChunk:
    checklist_id: UUID
    checklist_title: str
    item_number: int
    description: str
    content: str
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RunbookChunk:
    runbook_id: UUID
    runbook_title: str
    step_number: int
    action: str
    content: str
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class GenericChunk:
    node_id: UUID
    node_title: str
    chunk_index: int
    content: str
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)


class ProceduralChunker:
    """
    Chunks SOPs, checklists, and runbooks in a step-aware way.
    Standard token-based chunking destroys SOPs by splitting across chunks.
    Each chunk contains:
      - The step/item itself
      - SOP/checklist title and purpose (always present)
      - Previous step summary (so agents know what happened before)
      - Next step preview (so agents know what comes after)
    """

    def __init__(self, max_chunk_tokens: int = 512):
        self.max_chunk_tokens = max_chunk_tokens

    def chunk_sop(self, sop: SOP) -> list[SOPChunk]:
        chunks = []
        steps = sop.steps
        for i, step in enumerate(steps):
            prev_summary = ""
            if i > 0:
                prev = steps[i - 1]
                prev_summary = f"Previous step ({prev.step_number}. {prev.title}): {prev.description[:120]}..."

            next_preview = ""
            if i < len(steps) - 1:
                nxt = steps[i + 1]
                next_preview = f"Next step ({nxt.step_number}. {nxt.title}): {nxt.description[:80]}..."

            tools_str = ", ".join(step.tools_required) if step.tools_required else "none"
            role_str = step.responsible_role or "unspecified"

            content = (
                f"SOP: {sop.title}\n"
                f"Purpose: {sop.purpose}\n"
                f"Scope: {sop.scope}\n"
                f"---\n"
                f"Step {step.step_number} of {len(steps)}: {step.title}\n"
                f"Description: {step.description}\n"
                f"Responsible role: {role_str}\n"
                f"Tools required: {tools_str}\n"
            )
            if step.decision_points:
                content += f"Decision points: {', '.join(step.decision_points)}\n"
            if step.on_failure:
                content += f"On failure: {step.on_failure}\n"
            if step.estimated_duration_minutes:
                content += f"Estimated duration: {step.estimated_duration_minutes} minutes\n"
            if prev_summary:
                content += f"---\n{prev_summary}\n"
            if next_preview:
                content += f"{next_preview}\n"

            # Handle substeps
            if step.substeps:
                content += "Substeps:\n"
                for substep in step.substeps:
                    content += f"  {substep.step_number}. {substep.title}: {substep.description}\n"

            chunks.append(SOPChunk(
                sop_id=sop.id,
                sop_title=sop.title,
                step_number=step.step_number,
                step_title=step.title,
                content=content,
                metadata={
                    "org_id": sop.org_id,
                    "tags": sop.tags,
                    "roles_involved": sop.roles_involved,
                    "tools_required": sop.tools_required,
                }
            ))
        return chunks

    def chunk_checklist(self, checklist: Checklist) -> list[ChecklistChunk]:
        chunks = []
        items = checklist.items
        for i, item in enumerate(items):
            prev_summary = ""
            if i > 0:
                prev = items[i - 1]
                prev_summary = f"Previous item ({prev.item_number}): {prev.description[:100]}..."

            next_preview = ""
            if i < len(items) - 1:
                nxt = items[i + 1]
                next_preview = f"Next item ({nxt.item_number}): {nxt.description[:80]}..."

            content = (
                f"Checklist: {checklist.title}\n"
                f"Purpose: {checklist.purpose}\n"
                f"---\n"
                f"Item {item.item_number} of {len(items)}: {item.description}\n"
                f"Required: {'Yes' if item.required else 'No'}\n"
            )
            if item.verification_method:
                content += f"Verification: {item.verification_method}\n"
            if item.responsible_role:
                content += f"Responsible: {item.responsible_role}\n"
            if item.time_estimate_minutes:
                content += f"Time estimate: {item.time_estimate_minutes} minutes\n"
            if item.notes:
                content += f"Notes: {item.notes}\n"
            if prev_summary:
                content += f"---\n{prev_summary}\n"
            if next_preview:
                content += f"{next_preview}\n"

            chunks.append(ChecklistChunk(
                checklist_id=checklist.id,
                checklist_title=checklist.title,
                item_number=item.item_number,
                description=item.description,
                content=content,
                metadata={
                    "org_id": checklist.org_id,
                    "tags": checklist.tags,
                    "frequency": checklist.frequency,
                    "trigger": checklist.trigger,
                }
            ))
        return chunks

    def chunk_runbook(self, runbook: Runbook) -> list[RunbookChunk]:
        chunks = []
        steps = runbook.steps
        for i, step in enumerate(steps):
            prev_summary = ""
            if i > 0:
                prev = steps[i - 1]
                prev_summary = f"Previous step ({prev.step_number}): {prev.action[:100]}..."

            next_preview = ""
            if i < len(steps) - 1:
                nxt = steps[i + 1]
                next_preview = f"Next step ({nxt.step_number}): {nxt.action[:80]}..."

            content = (
                f"Runbook: {runbook.title}\n"
                f"System: {runbook.system}\n"
                f"Scenario: {runbook.scenario}\n"
                f"Severity: {runbook.severity or 'unspecified'}\n"
                f"---\n"
                f"Step {step.step_number} of {len(steps)}: {step.action}\n"
            )
            if step.command:
                content += f"Command: {step.command}\n"
            if step.expected_output:
                content += f"Expected output: {step.expected_output}\n"
            if step.on_success:
                content += f"On success: {step.on_success}\n"
            if step.on_failure:
                content += f"On failure: {step.on_failure}\n"
            if step.rollback:
                content += f"Rollback: {step.rollback}\n"
            if prev_summary:
                content += f"---\n{prev_summary}\n"
            if next_preview:
                content += f"{next_preview}\n"

            chunks.append(RunbookChunk(
                runbook_id=runbook.id,
                runbook_title=runbook.title,
                step_number=step.step_number,
                action=step.action,
                content=content,
                metadata={
                    "org_id": runbook.org_id,
                    "tags": runbook.tags,
                    "system": runbook.system,
                    "severity": runbook.severity,
                }
            ))
        return chunks

    def chunk_generic(self, node: CivilizationNode, text: str,
                      overlap_tokens: int = 50) -> list[GenericChunk]:
        """
        Token-based chunking for non-procedural content.
        Uses simple word-based approximation (1 token ~ 0.75 words).
        """
        words = text.split()
        words_per_chunk = int(self.max_chunk_tokens * 0.75)
        overlap_words = int(overlap_tokens * 0.75)

        chunks = []
        start = 0
        chunk_index = 0

        while start < len(words):
            end = min(start + words_per_chunk, len(words))
            chunk_text = " ".join(words[start:end])

            # Add node context header
            content = (
                f"{node.node_type.value.upper()}: {node.title}\n"
                f"---\n"
                f"{chunk_text}"
            )

            chunks.append(GenericChunk(
                node_id=node.id,
                node_title=node.title,
                chunk_index=chunk_index,
                content=content,
                metadata={
                    "org_id": node.org_id,
                    "node_type": node.node_type.value,
                    "tags": node.tags,
                }
            ))

            if end >= len(words):
                break
            start = end - overlap_words
            chunk_index += 1

        return chunks
