"""
HyperState Schema — Pydantic v2 data models for HyperClaw state management.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

Domain = Literal["personal", "business", "scientific", "creative", "recursive"]

TaskType = Literal[
    "code", "research", "analysis", "synthesis",
    "planning", "health", "finance", "scientific",
    "routing", "classification", "quick_lookup",
    "summarization", "draft", "triage", "status_check",
]


# ── Sub-models ─────────────────────────────────────────────────────────────────

class Task(BaseModel):
    goal: str
    constraints: list[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=0, le=10)
    task_type: TaskType = "research"


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    statement: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExperimentEntry(BaseModel):
    method: str
    model_used: str
    result: str = ""
    certified: bool = False
    test_trace: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentScore(BaseModel):
    attempts: int = 0
    successes: int = 0
    task_type: str = "general"

    @property
    def win_rate(self) -> float:
        return self.successes / self.attempts if self.attempts > 0 else 0.0


class ModelScore(BaseModel):
    attempts: int = 0
    successes: int = 0

    @property
    def win_rate(self) -> float:
        return self.successes / self.attempts if self.attempts > 0 else 0.0


class CertifiedMethod(BaseModel):
    method_id: str
    validated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    performance: dict[str, Any] = Field(default_factory=dict)
    model_used: str = ""


class RecursiveResearch(BaseModel):
    active: bool = False
    domains_monitored: list[str] = Field(default_factory=list)
    last_sweep: datetime | None = None
    discoveries_this_week: int = 0


# ── HyperState ─────────────────────────────────────────────────────────────────

class HyperState(BaseModel):
    state_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    domain: Domain = "business"
    task: Task = Field(default_factory=lambda: Task(goal=""))
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    experiment_log: list[ExperimentEntry] = Field(default_factory=list)
    agent_scores: dict[str, AgentScore] = Field(default_factory=dict)
    model_scores: dict[str, dict[str, ModelScore]] = Field(default_factory=dict)
    certified_methods: list[CertifiedMethod] = Field(default_factory=list)
    routing_weights: dict[str, float] = Field(default_factory=dict)
    recursive_research: RecursiveResearch = Field(default_factory=RecursiveResearch)
    state_version: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"arbitrary_types_allowed": True}

    def _bump_version(self) -> None:
        self.state_version += 1
        self.last_updated = datetime.now(timezone.utc)

    def certify_method(
        self,
        method_id: str,
        test_trace: str,
        result: str,
        model_used: str,
    ) -> CertifiedMethod:
        """
        Certify a method if test_trace and result are non-empty.
        Raises CertificationError if validation fails.
        """
        # Import here to avoid circular at module level
        from .certifier import CertificationError

        if not test_trace or not test_trace.strip():
            raise CertificationError(
                f"Certification failed for '{method_id}': test_trace is empty. "
                "A non-empty test trace is required for certification."
            )
        if not result or not result.strip():
            raise CertificationError(
                f"Certification failed for '{method_id}': result is empty. "
                "A non-empty result is required for certification."
            )

        cm = CertifiedMethod(
            method_id=method_id,
            model_used=model_used,
            performance={"test_trace_length": len(test_trace), "result_length": len(result)},
        )
        self.certified_methods.append(cm)
        self._bump_version()
        return cm

    def get_best_model(self, task_type: str) -> str:
        """
        Return the model_id with the highest successes/attempts ratio for
        the given task_type. Returns 'claude-sonnet-4-6' if no data exists.
        """
        best_model = "claude-sonnet-4-6"
        best_rate = -1.0

        for model_id, task_scores in self.model_scores.items():
            score = task_scores.get(task_type)
            if score is None:
                continue
            if score.attempts == 0:
                continue
            rate = score.successes / score.attempts
            if rate > best_rate:
                best_rate = rate
                best_model = model_id

        return best_model
