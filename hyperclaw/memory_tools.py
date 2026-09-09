"""Bind the existing memory tool schemas to the runtime's memory owner."""

import asyncio
from collections.abc import Awaitable, Callable
import inspect
import json
from typing import Any

from .memory_manager import Memory, MemoryManager


def _record(memory: Memory) -> dict:
    return {
        "id": memory.id, "content": memory.content, "source": memory.source,
        "domain": memory.domain, "metadata": memory.metadata,
        "created_at": memory.created_at.isoformat(),
    }


def bind_memory_tools(
    memory: MemoryManager, execute: Callable[[str, dict], Any], *, session_id: str | None = None,
) -> Callable[[str, dict], Awaitable[Any]]:
    """Route memory operations to the owner and preserve other executor behavior."""
    async def execute_tool(name: str, input_data: dict) -> Any:
        if name == "memory_store":
            identifier = await memory.remember(
                input_data["content"], domain=input_data.get("domain"),
                source=input_data.get("source", "conversation"),
            )
            return f"Stored memory {identifier}"
        if name == "memory_search":
            memories = await memory.recall(
                input_data["query"], limit=input_data.get("limit", 5), domain=input_data.get("domain"),
                session_id=session_id,
            )
            return json.dumps([_record(item) for item in memories], indent=2) if memories else "No memories found"
        if name == "memory_forget":
            identifier = input_data.get("memory_id")
            if identifier:
                return "Deleted 1 memory" if await memory.forget(identifier, session_id=session_id) else "Memory not found"
            if input_data.get("query"):
                matches = await memory.recall(input_data["query"], limit=1, session_id=session_id)
                if matches and await memory.forget(matches[0].id, session_id=session_id):
                    return f"Deleted memory {matches[0].id}: {matches[0].content[:50]}..."
                return "No matching memory found"
            return "Provide memory_id or query"
        if name == "memory_list":
            memories = await memory.list_memories(
                limit=input_data.get("limit", 20), domain=input_data.get("domain"),
                session_id=session_id,
            )
            return json.dumps([_record(item) for item in memories], indent=2) if memories else "No memories stored"
        if name == "memory_stats":
            return json.dumps(await memory.memory_stats(session_id=session_id), indent=2)
        if inspect.iscoroutinefunction(execute):
            return await execute(name, input_data)
        result = await asyncio.to_thread(execute, name, input_data)
        return await result if inspect.isawaitable(result) else result

    return execute_tool
