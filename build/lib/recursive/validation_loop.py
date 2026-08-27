"""
ValidationLoop — validates hypotheses through empirical testing via ClaudeCodeSubagent.
"""
from __future__ import annotations
import logging
from core.hyperstate.schema import HyperState, Hypothesis

log = logging.getLogger("hyperclaw.validation_loop")


class ValidationLoop:
    """Validates hypotheses via empirical testing using ClaudeCodeSubagent."""

    async def validate(self, state: HyperState, hypothesis: Hypothesis) -> bool:
        """
        Validate a hypothesis by generating a test script and executing it.
        Returns True if validation succeeds.
        """
        log.info(f"Validating hypothesis: {hypothesis.id} — '{hypothesis.statement}'")
        try:
            from models.claude_code_subagent import ClaudeCodeSubagent
            from models.claude_client import ClaudeClient
            import os
            client = ClaudeClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            subagent = ClaudeCodeSubagent(client)
            result = await subagent.run(
                f"Write a Python test to validate this hypothesis: {hypothesis.statement}",
                context="Hypothesis validation for HyperClaw",
                max_iterations=3,
            )
            hypothesis.confidence = 0.9 if result.success else 0.2
            state._bump_version()
            return result.success
        except Exception as e:
            log.warning(f"ValidationLoop: validation failed for {hypothesis.id}: {e}")
            return False
