"""
HyperClaw Memory Agent
Processes daily logs into summarized knowledge.
Runs on schedule or on-demand via /api/memory/consolidate.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("hyperclaw.memory")

# Paths - use environment variable or default
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
MEMORY_PATH = HYPERCLAW_ROOT / "memory"
WORKSPACE_PATH = HYPERCLAW_ROOT / "workspace"
KNOWLEDGE_FILE = MEMORY_PATH / "KNOWLEDGE.md"
SUMMARY_FILE = MEMORY_PATH / "summary.md"

MODEL = os.environ.get("HYPERCLAW_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = 4096


class MemoryAgent:
    """Memory consolidation engine - summarizes daily logs."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self._last_run: Optional[datetime] = None

    async def consolidate(self, days_back: int = 1) -> dict:
        """
        Main consolidation routine.
        1. Read recent daily logs
        2. Extract key facts and decisions
        3. Update knowledge file
        """
        logger.info(f"Memory consolidation starting (days_back={days_back})")

        results = {
            "logs_processed": 0,
            "facts_extracted": 0,
            "errors": [],
        }

        try:
            logs = self._gather_logs(days_back)
            results["logs_processed"] = len(logs)

            if not logs:
                logger.info("No logs to process")
                return results

            facts = await self._extract_facts(logs)
            if facts:
                self._append_knowledge(facts)
                results["facts_extracted"] = len(facts)

            self._last_run = datetime.now()
            logger.info(f"Memory consolidation complete: {results}")

        except Exception as e:
            logger.error(f"Consolidation error: {e}", exc_info=True)
            results["errors"].append(str(e))

        return results

    def _gather_logs(self, days_back: int) -> list[dict]:
        """Read recent daily logs."""
        logs = []
        today = datetime.now()

        for i in range(days_back):
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")

            for base in [MEMORY_PATH, WORKSPACE_PATH / "memory"]:
                log_path = base / f"{date_str}.md"
                if log_path.exists():
                    try:
                        content = log_path.read_text(encoding="utf-8")
                        if content.strip():
                            logs.append({
                                "date": date_str,
                                "path": str(log_path),
                                "content": content[:50000],
                            })
                    except Exception as e:
                        logger.warning(f"Failed to read {log_path}: {e}")

        return logs

    async def _extract_facts(self, logs: list[dict]) -> list[str]:
        """Extract key facts and decisions from logs."""
        combined = "\n\n---\n\n".join(
            f"## {log['date']}\n{log['content']}" for log in logs
        )

        prompt = f"""Analyze these daily logs and extract KEY FACTS that should be remembered.

Extract:
- Decisions made
- New information learned
- Completed tasks
- Important changes

Format each as:
[YYYY-MM-DD] Brief description

Do NOT include:
- Routine tasks
- In-progress work
- Technical debugging

Return only extracted facts, one per line. If none, return "NONE".

LOGS:
{combined}"""

        try:
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            if text == "NONE":
                return []

            facts = [
                line.strip() for line in text.split("\n")
                if line.strip() and line.strip().startswith("[")
            ]
            return facts
        except Exception as e:
            logger.error(f"Fact extraction failed: {e}")
            return []

    def _append_knowledge(self, facts: list[str]) -> None:
        """Append new facts to knowledge file."""
        if not facts:
            return

        KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if KNOWLEDGE_FILE.exists():
            existing = KNOWLEDGE_FILE.read_text(encoding="utf-8")

        new_facts = []
        for fact in facts:
            match = re.match(r"\[(\d{4}-\d{2}-\d{2})\]\s*(.+)", fact)
            if match:
                content = match.group(2).lower()
                if content not in existing.lower():
                    new_facts.append(fact)

        if new_facts:
            with open(KNOWLEDGE_FILE, "a", encoding="utf-8") as f:
                f.write("\n")
                for fact in new_facts:
                    f.write(f"- {fact}\n")
            logger.info(f"Added {len(new_facts)} facts to KNOWLEDGE.md")

    def status(self) -> dict:
        """Get agent status."""
        return {
            "name": "memory_agent",
            "status": "active",
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "knowledge_file": str(KNOWLEDGE_FILE),
        }


# Singleton
_agent: Optional[MemoryAgent] = None


def get_memory_agent() -> MemoryAgent:
    """Get or create memory agent singleton."""
    global _agent
    if _agent is None:
        _agent = MemoryAgent()
    return _agent


async def run_consolidation(days_back: int = 1) -> dict:
    """Run memory consolidation."""
    agent = get_memory_agent()
    return await agent.consolidate(days_back)


# Backwards compatibility
Prometheus = MemoryAgent
get_prometheus = get_memory_agent


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    result = asyncio.run(run_consolidation(days))
    print(json.dumps(result, indent=2))
