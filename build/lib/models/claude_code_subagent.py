"""
ClaudeCodeSubagent — empirical code certification loop.
Generates code via Claude, executes it in subprocess, iterates on failures.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from core.hyperstate.certifier import CertificationError, Certifier
from core.hyperstate.schema import CertifiedMethod, ExperimentEntry
from .claude_client import ClaudeClient

log = logging.getLogger("hyperclaw.claude_code_subagent")

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)

_GENERATE_SYSTEM = """You are a precise Python code generator. 
When given a task, output ONLY a complete, runnable Python script in a ```python code block.
Include inline tests using assert statements or a simple main() function.
The code must be self-contained and executable."""

_FIX_SYSTEM = """You are a Python debugging expert.
You will be given code that failed along with its error output.
Output ONLY the fixed, complete, runnable Python script in a ```python code block.
Do not explain — just fix the code."""


@dataclass
class SubagentResult:
    code: str
    test_trace: str
    success: bool
    iterations: int
    total_time_seconds: float
    error: str | None = None


class ClaudeCodeSubagent:
    """
    Iterative code generation and certification agent.

    Workflow:
    1. Ask Claude to generate code for a task
    2. Execute generated code in a subprocess (30s timeout)
    3. If tests pass → return success
    4. If tests fail → feed error back to Claude with "Fix this:" prompt
    5. Repeat up to max_iterations
    6. On final failure → return SubagentResult(success=False)
    """

    def __init__(self, claude_client: ClaudeClient) -> None:
        self._claude = claude_client
        self._certifier = Certifier()

    async def run(
        self,
        task: str,
        context: str = "",
        max_iterations: int = 5,
    ) -> SubagentResult:
        """
        Run the code generation → execution → fix loop.
        Returns a SubagentResult with success/failure and full test trace.
        """
        t0 = time.time()
        messages: list[dict] = []
        code = ""
        test_trace = ""
        last_error: str | None = None

        # Initial generation request
        user_msg = f"Task: {task}"
        if context:
            user_msg += f"\n\nContext:\n{context}"
        messages.append({"role": "user", "content": user_msg})

        for iteration in range(1, max_iterations + 1):
            log.info(f"Subagent iteration {iteration}/{max_iterations}")

            # Generate / fix
            response = await self._claude.chat(messages, system=_GENERATE_SYSTEM if iteration == 1 else _FIX_SYSTEM)
            messages.append({"role": "assistant", "content": response})

            # Extract code block
            code = _extract_code(response)
            if not code:
                last_error = "No code block found in Claude response"
                messages.append({"role": "user", "content": f"Fix this: {last_error}\n\nOutput a complete Python script in a ```python block."})
                continue

            # Execute
            stdout, stderr, returncode = await _run_subprocess(code, timeout=30)
            test_trace = f"=== Iteration {iteration} ===\n{stdout}"
            if stderr:
                test_trace += f"\n--- stderr ---\n{stderr}"
            test_trace += f"\n--- exit code: {returncode} ---"

            if returncode == 0:
                total_time = time.time() - t0
                log.info(f"Subagent succeeded on iteration {iteration} in {total_time:.1f}s")
                return SubagentResult(
                    code=code,
                    test_trace=test_trace,
                    success=True,
                    iterations=iteration,
                    total_time_seconds=total_time,
                    error=None,
                )
            else:
                last_error = f"Exit code {returncode}\nstdout: {stdout}\nstderr: {stderr}"
                messages.append({
                    "role": "user",
                    "content": f"Fix this:\n\n{last_error}\n\nOutput the complete fixed Python script in a ```python block.",
                })

        total_time = time.time() - t0
        log.warning(f"Subagent exhausted {max_iterations} iterations. Last error: {last_error}")
        return SubagentResult(
            code=code,
            test_trace=test_trace,
            success=False,
            iterations=max_iterations,
            total_time_seconds=total_time,
            error=last_error,
        )

    async def certify(
        self,
        task: str,
        context: str = "",
    ) -> tuple[str, CertifiedMethod]:
        """
        Run the subagent loop and certify the result if successful.

        Returns:
            (code, CertifiedMethod) on success
        Raises:
            CertificationError if the run failed or certification criteria not met
        """
        result = await self.run(task, context)
        if not result.success:
            raise CertificationError(
                f"Subagent failed after {result.iterations} iterations: {result.error}"
            )

        entry = ExperimentEntry(
            method=f"claude_code_subagent:{task[:60]}",
            model_used=self._claude.model,
            result=result.code[:500],
            certified=True,
            test_trace=result.test_trace,
            cost_usd=0.0,
            latency_ms=result.total_time_seconds * 1000,
        )
        certified_method = self._certifier.certify(entry)
        return result.code, certified_method


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_code(text: str) -> str:
    """Extract the first Python code block from a markdown-formatted response."""
    match = _CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    # Fallback: return the whole response if it looks like code
    if text.strip().startswith(("def ", "import ", "class ", "#")):
        return text.strip()
    return ""


async def _run_subprocess(code: str, timeout: float = 30.0) -> tuple[str, str, int]:
    """
    Execute Python code in a subprocess. Returns (stdout, stderr, returncode).
    Times out after `timeout` seconds.
    """
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout_b.decode(), stderr_b.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return "", f"Process timed out after {timeout}s", 1
