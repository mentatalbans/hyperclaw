#!/usr/bin/env python3
"""
Assistant Planning System — Goal decomposition and autonomous execution.
Breaks complex goals into subtasks, tracks progress, adapts plans.
"""

import os
import sys
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))

HYPERCLAW_ROOT = Path(__file__).parent.parent
PLANS_DIR = HYPERCLAW_ROOT / "workspace" / "plans"
PLANS_DIR.mkdir(parents=True, exist_ok=True)


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class Task:
    """A single task in a plan."""

    def __init__(
        self,
        title: str,
        description: str = "",
        depends_on: List[str] = None,
        task_id: str = None
    ):
        self.id = task_id or str(uuid.uuid4())[:8]
        self.title = title
        self.description = description
        self.status = TaskStatus.PENDING
        self.depends_on = depends_on or []
        self.result = None
        self.error = None
        self.started_at = None
        self.completed_at = None
        self.retries = 0

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "depends_on": self.depends_on,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retries": self.retries
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Task":
        task = cls(
            title=data["title"],
            description=data.get("description", ""),
            depends_on=data.get("depends_on", []),
            task_id=data["id"]
        )
        task.status = TaskStatus(data.get("status", "pending"))
        task.result = data.get("result")
        task.error = data.get("error")
        task.started_at = data.get("started_at")
        task.completed_at = data.get("completed_at")
        task.retries = data.get("retries", 0)
        return task


class Plan:
    """A plan with multiple tasks."""

    def __init__(self, goal: str, plan_id: str = None):
        self.id = plan_id or str(uuid.uuid4())[:8]
        self.goal = goal
        self.tasks: Dict[str, Task] = {}
        self.task_order: List[str] = []
        self.status = "planning"
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.metadata: Dict[str, Any] = {}

    def add_task(self, task: Task) -> str:
        """Add a task to the plan."""
        self.tasks[task.id] = task
        self.task_order.append(task.id)
        self.updated_at = datetime.now().isoformat()
        return task.id

    def get_next_tasks(self) -> List[Task]:
        """Get tasks that are ready to execute (dependencies met)."""
        ready = []
        for task_id in self.task_order:
            task = self.tasks[task_id]
            if task.status != TaskStatus.PENDING:
                continue

            # Check dependencies
            deps_met = True
            for dep_id in task.depends_on:
                if dep_id in self.tasks:
                    dep_task = self.tasks[dep_id]
                    if dep_task.status != TaskStatus.COMPLETED:
                        deps_met = False
                        break

            if deps_met:
                ready.append(task)

        return ready

    def get_progress(self) -> Dict:
        """Get plan progress."""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
        in_progress = sum(1 for t in self.tasks.values() if t.status == TaskStatus.IN_PROGRESS)

        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "pending": total - completed - failed - in_progress,
            "percent": round(completed / total * 100) if total > 0 else 0
        }

    def is_complete(self) -> bool:
        """Check if plan is complete."""
        return all(
            t.status in [TaskStatus.COMPLETED, TaskStatus.SKIPPED]
            for t in self.tasks.values()
        )

    def is_blocked(self) -> bool:
        """Check if plan is blocked (failed dependency)."""
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                for dep_id in task.depends_on:
                    if dep_id in self.tasks and self.tasks[dep_id].status == TaskStatus.FAILED:
                        return True
        return False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
            "task_order": self.task_order,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "progress": self.get_progress()
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Plan":
        plan = cls(goal=data["goal"], plan_id=data["id"])
        plan.status = data.get("status", "planning")
        plan.task_order = data.get("task_order", [])
        plan.created_at = data.get("created_at")
        plan.updated_at = data.get("updated_at")
        plan.metadata = data.get("metadata", {})

        for tid, tdata in data.get("tasks", {}).items():
            plan.tasks[tid] = Task.from_dict(tdata)

        return plan

    def save(self):
        """Save plan to disk."""
        path = PLANS_DIR / f"{self.id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, plan_id: str) -> Optional["Plan"]:
        """Load plan from disk."""
        path = PLANS_DIR / f"{plan_id}.json"
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        return None


class Planner:
    """The planning engine."""

    def __init__(self):
        self.active_plan: Optional[Plan] = None

    def create_plan(self, goal: str, tasks: List[Dict] = None) -> Plan:
        """Create a new plan with optional initial tasks."""
        plan = Plan(goal=goal)

        if tasks:
            for t in tasks:
                task = Task(
                    title=t["title"],
                    description=t.get("description", ""),
                    depends_on=t.get("depends_on", [])
                )
                plan.add_task(task)

        plan.status = "ready"
        plan.save()
        self.active_plan = plan
        return plan

    def decompose_goal(self, goal: str) -> List[Dict]:
        """Use AI to decompose a goal into tasks.
        This would call Claude to generate the task breakdown."""
        # For now, return a simple template
        # In production, this would call the Claude API
        return [
            {"title": f"Analyze: {goal}", "description": "Understand the requirements"},
            {"title": "Research", "description": "Gather necessary information"},
            {"title": "Plan approach", "description": "Design the solution"},
            {"title": "Execute", "description": "Implement the solution"},
            {"title": "Verify", "description": "Check the results", "depends_on": []},
        ]

    def start_task(self, plan_id: str, task_id: str) -> str:
        """Mark a task as started."""
        plan = Plan.load(plan_id)
        if not plan or task_id not in plan.tasks:
            return "Task not found"

        task = plan.tasks[task_id]
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = datetime.now().isoformat()
        plan.updated_at = datetime.now().isoformat()
        plan.save()
        return f"Started: {task.title}"

    def complete_task(self, plan_id: str, task_id: str, result: str = None) -> str:
        """Mark a task as completed."""
        plan = Plan.load(plan_id)
        if not plan or task_id not in plan.tasks:
            return "Task not found"

        task = plan.tasks[task_id]
        task.status = TaskStatus.COMPLETED
        task.result = result
        task.completed_at = datetime.now().isoformat()
        plan.updated_at = datetime.now().isoformat()

        # Check if plan is complete
        if plan.is_complete():
            plan.status = "completed"

        plan.save()
        return f"Completed: {task.title}"

    def fail_task(self, plan_id: str, task_id: str, error: str = None) -> str:
        """Mark a task as failed."""
        plan = Plan.load(plan_id)
        if not plan or task_id not in plan.tasks:
            return "Task not found"

        task = plan.tasks[task_id]
        task.status = TaskStatus.FAILED
        task.error = error
        task.completed_at = datetime.now().isoformat()
        plan.updated_at = datetime.now().isoformat()

        if plan.is_blocked():
            plan.status = "blocked"

        plan.save()
        return f"Failed: {task.title}"

    def retry_task(self, plan_id: str, task_id: str) -> str:
        """Retry a failed task."""
        plan = Plan.load(plan_id)
        if not plan or task_id not in plan.tasks:
            return "Task not found"

        task = plan.tasks[task_id]
        task.status = TaskStatus.PENDING
        task.error = None
        task.retries += 1
        plan.status = "ready"
        plan.save()
        return f"Retrying: {task.title} (attempt {task.retries + 1})"

    def get_plan(self, plan_id: str) -> Optional[Dict]:
        """Get plan details."""
        plan = Plan.load(plan_id)
        return plan.to_dict() if plan else None

    def list_plans(self) -> List[Dict]:
        """List all plans."""
        plans = []
        for path in PLANS_DIR.glob("*.json"):
            try:
                plan = Plan.load(path.stem)
                if plan:
                    plans.append({
                        "id": plan.id,
                        "goal": plan.goal[:50] + "..." if len(plan.goal) > 50 else plan.goal,
                        "status": plan.status,
                        "progress": plan.get_progress()
                    })
            except:
                continue
        return plans

    def delete_plan(self, plan_id: str) -> str:
        """Delete a plan."""
        path = PLANS_DIR / f"{plan_id}.json"
        if path.exists():
            path.unlink()
            return f"Deleted plan {plan_id}"
        return "Plan not found"


# Global planner instance
planner = Planner()


# CLI functions for TUI integration
def plan_create(goal: str, tasks: List[Dict] = None) -> Dict:
    """Create a new plan."""
    if not tasks:
        # Auto-decompose goal into tasks
        tasks = planner.decompose_goal(goal)

    plan = planner.create_plan(goal, tasks)
    return plan.to_dict()


def plan_add_task(plan_id: str, title: str, description: str = "", depends_on: List[str] = None) -> str:
    """Add a task to a plan."""
    plan = Plan.load(plan_id)
    if not plan:
        return "Plan not found"

    task = Task(title=title, description=description, depends_on=depends_on or [])
    plan.add_task(task)
    plan.save()
    return f"Added task {task.id}: {title}"


def plan_status(plan_id: str) -> Dict:
    """Get plan status."""
    return planner.get_plan(plan_id) or {"error": "Plan not found"}


def plan_next(plan_id: str) -> List[Dict]:
    """Get next tasks ready to execute."""
    plan = Plan.load(plan_id)
    if not plan:
        return []

    tasks = plan.get_next_tasks()
    return [t.to_dict() for t in tasks]


def plan_update(plan_id: str, task_id: str, status: str, result: str = None) -> str:
    """Update a task status."""
    if status == "completed":
        return planner.complete_task(plan_id, task_id, result)
    elif status == "failed":
        return planner.fail_task(plan_id, task_id, result)
    elif status == "in_progress":
        return planner.start_task(plan_id, task_id)
    elif status == "retry":
        return planner.retry_task(plan_id, task_id)
    return f"Unknown status: {status}"


def plan_list() -> List[Dict]:
    """List all plans."""
    return planner.list_plans()


def plan_delete(plan_id: str) -> str:
    """Delete a plan."""
    return planner.delete_plan(plan_id)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: planner.py <create|status|list|next> [args]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create":
        goal = " ".join(sys.argv[2:]) or "Test goal"
        result = plan_create(goal)
        print(json.dumps(result, indent=2))

    elif cmd == "status":
        plan_id = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(plan_status(plan_id), indent=2))

    elif cmd == "list":
        print(json.dumps(plan_list(), indent=2))

    elif cmd == "next":
        plan_id = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(plan_next(plan_id), indent=2))

    else:
        print(f"Unknown command: {cmd}")
