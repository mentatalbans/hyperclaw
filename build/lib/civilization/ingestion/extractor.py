"""
Entity and metadata extraction from documents for Civilization Knowledge Layer.
Uses LLM-based extraction for structured information.
"""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class ExtractedEntity:
    entity_type: str  # "person", "role", "tool", "system", "process", "metric"
    name: str
    context: str
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ExtractedMetadata:
    title: str | None = None
    purpose: str | None = None
    owner: str | None = None
    department: str | None = None
    tags: list[str] = field(default_factory=list)
    roles_mentioned: list[str] = field(default_factory=list)
    tools_mentioned: list[str] = field(default_factory=list)
    systems_mentioned: list[str] = field(default_factory=list)
    metrics_mentioned: list[str] = field(default_factory=list)
    estimated_complexity: str | None = None  # "low", "medium", "high"
    document_type: str | None = None


class MetadataExtractor:
    """
    Extracts metadata from document text using pattern matching and heuristics.
    For LLM-based extraction, use with an agent wrapper.
    """

    # Common role patterns
    ROLE_PATTERNS = [
        r"(?:responsible|owner|lead|manager|coordinator|assigned to|handled by)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"([A-Z][a-z]+\s+(?:Manager|Director|Lead|Coordinator|Specialist|Engineer|Analyst|Administrator))",
    ]

    # Common tool patterns
    TOOL_PATTERNS = [
        r"(?:using|via|through|in|with)\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)?)",
        r"(Slack|Jira|Confluence|Notion|Asana|Trello|GitHub|GitLab|Salesforce|HubSpot|Zendesk)",
    ]

    # System patterns
    SYSTEM_PATTERNS = [
        r"([A-Z][a-zA-Z0-9]+(?:DB|API|Service|System|Platform|Server))",
        r"(?:database|system|service|platform)\s*[:\-]?\s*([A-Z][a-zA-Z0-9]+)",
    ]

    def extract(self, text: str) -> ExtractedMetadata:
        """Extract metadata from raw document text."""
        metadata = ExtractedMetadata()

        # Extract title (first line or heading)
        lines = text.strip().split("\n")
        if lines:
            first_line = lines[0].strip()
            # Remove markdown heading markers
            if first_line.startswith("#"):
                first_line = re.sub(r"^#+\s*", "", first_line)
            if len(first_line) < 200:
                metadata.title = first_line

        # Extract purpose (look for common patterns)
        purpose_match = re.search(
            r"(?:purpose|objective|goal|overview)\s*[:\-]?\s*(.+?)(?:\n|$)",
            text, re.IGNORECASE
        )
        if purpose_match:
            metadata.purpose = purpose_match.group(1).strip()[:500]

        # Extract roles
        for pattern in self.ROLE_PATTERNS:
            matches = re.findall(pattern, text)
            metadata.roles_mentioned.extend(matches)
        metadata.roles_mentioned = list(set(metadata.roles_mentioned))

        # Extract tools
        for pattern in self.TOOL_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            metadata.tools_mentioned.extend([m for m in matches if len(m) > 2])
        metadata.tools_mentioned = list(set(metadata.tools_mentioned))

        # Extract systems
        for pattern in self.SYSTEM_PATTERNS:
            matches = re.findall(pattern, text)
            metadata.systems_mentioned.extend(matches)
        metadata.systems_mentioned = list(set(metadata.systems_mentioned))

        # Extract tags from hashtags
        hashtags = re.findall(r"#([a-zA-Z][a-zA-Z0-9_-]+)", text)
        metadata.tags = list(set(hashtags))

        # Estimate complexity based on length and structure
        word_count = len(text.split())
        step_count = len(re.findall(r"(?:step|item|\d+\.)\s", text, re.IGNORECASE))
        if word_count < 300 and step_count < 5:
            metadata.estimated_complexity = "low"
        elif word_count < 1000 and step_count < 15:
            metadata.estimated_complexity = "medium"
        else:
            metadata.estimated_complexity = "high"

        # Detect document type
        metadata.document_type = self._detect_document_type(text)

        return metadata

    def _detect_document_type(self, text: str) -> str | None:
        """Detect the type of document based on content patterns."""
        text_lower = text.lower()

        if any(kw in text_lower for kw in ["sop", "standard operating procedure", "procedure:"]):
            return "sop"
        if any(kw in text_lower for kw in ["checklist", "□", "☐", "[ ]", "[x]"]):
            return "checklist"
        if any(kw in text_lower for kw in ["runbook", "playbook", "incident", "on-call"]):
            return "runbook"
        if any(kw in text_lower for kw in ["job description", "responsibilities:", "qualifications:"]):
            return "job_description"
        if any(kw in text_lower for kw in ["org chart", "organization chart", "reporting structure"]):
            return "org_chart"
        if any(kw in text_lower for kw in ["workflow", "process flow", "→", "->"]):
            return "workflow"
        if any(kw in text_lower for kw in ["policy", "must comply", "prohibited", "required"]):
            return "policy"
        return "knowledge_article"


class EntityExtractor:
    """
    Extracts named entities and their relationships from text.
    """

    def __init__(self):
        self.entity_patterns = {
            "person": [
                r"([A-Z][a-z]+\s+[A-Z][a-z]+)",  # First Last
                r"@([a-zA-Z][a-zA-Z0-9_]+)",  # @username
            ],
            "email": [
                r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
            ],
            "url": [
                r"(https?://[^\s<>\"]+)",
            ],
            "metric": [
                r"(\d+(?:\.\d+)?%)",  # Percentages
                r"(\$\d+(?:,\d{3})*(?:\.\d{2})?)",  # Currency
                r"(\d+(?:\.\d+)?\s*(?:hours?|mins?|minutes?|days?|weeks?))",  # Time durations
            ],
            "date": [
                r"(\d{4}-\d{2}-\d{2})",  # ISO date
                r"(\d{1,2}/\d{1,2}/\d{2,4})",  # US date
            ],
        }

    def extract(self, text: str) -> list[ExtractedEntity]:
        """Extract all entities from text."""
        entities = []

        for entity_type, patterns in self.entity_patterns.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    # Get surrounding context (50 chars before and after)
                    start = max(0, match.start() - 50)
                    end = min(len(text), match.end() + 50)
                    context = text[start:end]

                    entities.append(ExtractedEntity(
                        entity_type=entity_type,
                        name=match.group(1) if match.lastindex else match.group(0),
                        context=context,
                        confidence=0.8,
                    ))

        # Deduplicate by name within each type
        seen = set()
        unique_entities = []
        for entity in entities:
            key = (entity.entity_type, entity.name)
            if key not in seen:
                seen.add(key)
                unique_entities.append(entity)

        return unique_entities

    def extract_relationships(self, text: str) -> list[dict]:
        """
        Extract relationships between entities.
        Returns list of {"source": str, "target": str, "relationship": str}
        """
        relationships = []

        # Pattern: "X reports to Y" or "X manages Y"
        report_patterns = [
            (r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+reports?\s+to\s+([A-Z][a-z]+\s+[A-Z][a-z]+)", "reports_to"),
            (r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+manages?\s+([A-Z][a-z]+\s+[A-Z][a-z]+)", "manages"),
            (r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+owns?\s+([A-Z][a-z]+)", "owns"),
            (r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+is\s+responsible\s+for\s+([A-Z][a-z]+)", "responsible_for"),
        ]

        for pattern, rel_type in report_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                relationships.append({
                    "source": match.group(1),
                    "target": match.group(2),
                    "relationship": rel_type,
                })

        return relationships
