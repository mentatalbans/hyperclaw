"""
Context injection for agent prompts.
Formats and injects organizational knowledge into agent context.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..schema import CivilizationNode, NodeType

logger = logging.getLogger(__name__)


class InjectionMode(str, Enum):
    FULL = "full"  # Include complete node content
    SUMMARY = "summary"  # Include only summaries
    REFERENCE = "reference"  # Include only titles and IDs
    STRUCTURED = "structured"  # Include structured data format
    MINIMAL = "minimal"  # Bare minimum context


@dataclass
class InjectionConfig:
    """Configuration for context injection."""
    mode: InjectionMode = InjectionMode.SUMMARY
    max_tokens: int = 2000
    include_metadata: bool = True
    include_relationships: bool = True
    priority_types: list[NodeType] | None = None
    format: str = "markdown"  # "markdown", "xml", "json"


@dataclass
class InjectionResult:
    """Result of context injection."""
    context: str
    nodes_included: int
    truncated: bool = False
    token_estimate: int = 0


class ContextInjector:
    """
    Injects civilization knowledge into agent prompts.
    Handles formatting, token budgeting, and prioritization.
    """

    PRIORITY_ORDER = [
        NodeType.SOP,
        NodeType.RUNBOOK,
        NodeType.CHECKLIST,
        NodeType.ROLE,
        NodeType.WORKFLOW,
        NodeType.POLICY,
        NodeType.JOB_DESCRIPTION,
        NodeType.CLIENT_PROFILE,
        NodeType.KNOWLEDGE_ARTICLE,
        NodeType.ORG_CHART,
        NodeType.PERSON,
        NodeType.PERSONAL_ROUTINE,
    ]

    def __init__(self, config: InjectionConfig | None = None):
        self.config = config or InjectionConfig()

    def inject(
        self,
        nodes: list[CivilizationNode],
        config_override: InjectionConfig | None = None,
    ) -> InjectionResult:
        """
        Generate injection context from nodes.

        Args:
            nodes: Nodes to include in context
            config_override: Optional config override

        Returns:
            InjectionResult with formatted context
        """
        config = config_override or self.config
        result = InjectionResult(context="", nodes_included=0)

        if not nodes:
            return result

        # Sort by priority
        sorted_nodes = self._prioritize_nodes(nodes, config)

        # Format based on mode
        formatter = self._get_formatter(config.format)
        sections = []
        total_tokens = 0

        for node in sorted_nodes:
            section = self._format_node(node, config)
            section_tokens = len(section) // 4  # Rough estimate

            if total_tokens + section_tokens > config.max_tokens:
                result.truncated = True
                break

            sections.append(section)
            total_tokens += section_tokens
            result.nodes_included += 1

        result.context = formatter(sections, config)
        result.token_estimate = total_tokens

        return result

    def _prioritize_nodes(
        self,
        nodes: list[CivilizationNode],
        config: InjectionConfig,
    ) -> list[CivilizationNode]:
        """Sort nodes by priority."""
        priority_types = config.priority_types or self.PRIORITY_ORDER

        def sort_key(node: CivilizationNode) -> tuple:
            try:
                type_priority = priority_types.index(node.node_type)
            except ValueError:
                type_priority = len(priority_types)
            return (type_priority, -node.updated_at.timestamp())

        return sorted(nodes, key=sort_key)

    def _format_node(
        self,
        node: CivilizationNode,
        config: InjectionConfig,
    ) -> str:
        """Format a single node based on injection mode."""
        if config.mode == InjectionMode.REFERENCE:
            return self._format_reference(node)
        elif config.mode == InjectionMode.MINIMAL:
            return self._format_minimal(node)
        elif config.mode == InjectionMode.SUMMARY:
            return self._format_summary(node, config)
        elif config.mode == InjectionMode.STRUCTURED:
            return self._format_structured(node, config)
        else:  # FULL
            return self._format_full(node, config)

    def _format_reference(self, node: CivilizationNode) -> str:
        """Format as reference only."""
        return f"- [{node.node_type.value}] {node.title} (ID: {node.id})"

    def _format_minimal(self, node: CivilizationNode) -> str:
        """Format with minimal information."""
        return f"{node.node_type.value.upper()}: {node.title}"

    def _format_summary(
        self,
        node: CivilizationNode,
        config: InjectionConfig,
    ) -> str:
        """Format as summary."""
        lines = [f"**{node.node_type.value.upper()}**: {node.title}"]

        node_dict = node.model_dump()

        # Add key fields based on node type
        if node.node_type == NodeType.SOP and "purpose" in node_dict:
            lines.append(f"Purpose: {node_dict['purpose']}")
            if "steps" in node_dict:
                step_count = len(node_dict["steps"])
                lines.append(f"Steps: {step_count} total")

        elif node.node_type == NodeType.ROLE:
            if "role_title" in node_dict:
                lines.append(f"Role: {node_dict['role_title']}")
            if "responsibilities" in node_dict:
                lines.append(f"Key responsibilities: {len(node_dict['responsibilities'])} defined")

        elif node.node_type == NodeType.RUNBOOK:
            if "scenario" in node_dict:
                lines.append(f"Scenario: {node_dict['scenario']}")
            if "severity" in node_dict:
                lines.append(f"Severity: {node_dict['severity']}")

        if config.include_metadata and node.tags:
            lines.append(f"Tags: {', '.join(node.tags[:5])}")

        return "\n".join(lines)

    def _format_structured(
        self,
        node: CivilizationNode,
        config: InjectionConfig,
    ) -> str:
        """Format as structured data."""
        import json
        data = {
            "type": node.node_type.value,
            "title": node.title,
            "id": str(node.id),
        }

        node_dict = node.model_dump()

        # Include type-specific fields
        if node.node_type == NodeType.SOP:
            data["purpose"] = node_dict.get("purpose", "")
            data["step_count"] = len(node_dict.get("steps", []))
            data["roles"] = node_dict.get("roles_involved", [])

        elif node.node_type == NodeType.ROLE:
            data["role_title"] = node_dict.get("role_title", "")
            data["department"] = node_dict.get("department", "")

        if config.include_metadata:
            data["tags"] = node.tags
            data["owner"] = node.owner_id

        return json.dumps(data, indent=2)

    def _format_full(
        self,
        node: CivilizationNode,
        config: InjectionConfig,
    ) -> str:
        """Format with full content."""
        lines = [
            f"## {node.node_type.value.upper()}: {node.title}",
            "",
        ]

        node_dict = node.model_dump()

        # Full content varies by type
        if node.node_type == NodeType.SOP:
            lines.append(f"**Purpose:** {node_dict.get('purpose', 'N/A')}")
            lines.append(f"**Scope:** {node_dict.get('scope', 'N/A')}")
            lines.append("")

            if "steps" in node_dict:
                lines.append("### Steps")
                for step in node_dict["steps"]:
                    lines.append(f"{step.get('step_number', '?')}. **{step.get('title', 'Step')}**")
                    lines.append(f"   {step.get('description', '')}")
                    if step.get("responsible_role"):
                        lines.append(f"   - Role: {step['responsible_role']}")
                    if step.get("tools_required"):
                        lines.append(f"   - Tools: {', '.join(step['tools_required'])}")
                    lines.append("")

        elif node.node_type == NodeType.ROLE:
            lines.append(f"**Title:** {node_dict.get('role_title', 'N/A')}")
            lines.append(f"**Department:** {node_dict.get('department', 'N/A')}")
            lines.append("")

            if "responsibilities" in node_dict:
                lines.append("### Responsibilities")
                for r in node_dict["responsibilities"]:
                    lines.append(f"- {r}")

            if "accountabilities" in node_dict:
                lines.append("")
                lines.append("### Accountabilities")
                for a in node_dict["accountabilities"]:
                    lines.append(f"- {a}")

        elif node.node_type == NodeType.CHECKLIST:
            lines.append(f"**Purpose:** {node_dict.get('purpose', 'N/A')}")
            lines.append("")

            if "items" in node_dict:
                lines.append("### Items")
                for item in node_dict["items"]:
                    req = "[Required]" if item.get("required") else "[Optional]"
                    lines.append(f"- {req} {item.get('description', '')}")

        if config.include_metadata:
            lines.append("")
            lines.append("---")
            lines.append(f"*Owner: {node.owner_id or 'Unassigned'}*")
            lines.append(f"*Tags: {', '.join(node.tags) if node.tags else 'None'}*")
            lines.append(f"*Last updated: {node.updated_at.isoformat()}*")

        return "\n".join(lines)

    def _get_formatter(self, format_type: str):
        """Get formatter function for output format."""
        if format_type == "xml":
            return self._format_as_xml
        elif format_type == "json":
            return self._format_as_json
        else:  # markdown
            return self._format_as_markdown

    def _format_as_markdown(
        self,
        sections: list[str],
        config: InjectionConfig,
    ) -> str:
        """Format sections as markdown."""
        header = "# Organizational Knowledge Context\n\n"
        return header + "\n\n---\n\n".join(sections)

    def _format_as_xml(
        self,
        sections: list[str],
        config: InjectionConfig,
    ) -> str:
        """Format sections as XML."""
        import html
        items = "\n".join(
            f"<knowledge_item>\n{html.escape(s)}\n</knowledge_item>"
            for s in sections
        )
        return f"<organizational_knowledge>\n{items}\n</organizational_knowledge>"

    def _format_as_json(
        self,
        sections: list[str],
        config: InjectionConfig,
    ) -> str:
        """Format sections as JSON array."""
        import json
        return json.dumps({"knowledge_items": sections}, indent=2)

    def build_system_prompt_section(
        self,
        nodes: list[CivilizationNode],
        section_name: str = "ORGANIZATIONAL_CONTEXT",
    ) -> str:
        """
        Build a section suitable for system prompts.
        """
        result = self.inject(nodes)
        if not result.context:
            return ""

        return f"""
<{section_name}>
{result.context}
</{section_name}>
"""
