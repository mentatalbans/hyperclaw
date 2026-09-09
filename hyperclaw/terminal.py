"""Terminal adapter for the canonical conversation runtime."""
import asyncio
import json
from pathlib import Path


def _load_legacy_history(path: Path) -> list[dict]:
    """Read completed legacy turns without replaying transient tool exchanges."""
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        history = json.loads(source)
        if not isinstance(history, list):
            raise ValueError("expected a list of messages")
        completed = []
        for message in history:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                raise ValueError("expected user or assistant messages")
            content = message.get("content")
            if isinstance(content, list):
                blocks = []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") not in (
                        "text", "image", "document", "tool_use", "tool_result", "thinking", "redacted_thinking"
                    ):
                        raise ValueError("invalid message content block")
                    if block["type"] == "text":
                        if not isinstance(block.get("text"), str):
                            raise ValueError("expected text in a text block")
                        blocks.append(block)
                    elif block["type"] in ("image", "document"):
                        blocks.append(block)
                content = ("\n".join(block["text"] for block in blocks)
                           if all(block["type"] == "text" for block in blocks) else blocks)
            elif not isinstance(content, str):
                raise ValueError("expected string or block-list message content")
            # Like the old history repair, remove empty messages and leading
            # assistant fragments. Tool blocks stay out of the durable window:
            # later trimming could otherwise orphan a previously valid pair.
            if content and (completed or message["role"] == "user"):
                completed.append({**message, "content": content})
        return completed
    except ValueError as exc:
        raise ValueError(f"Cannot import legacy session history from {path}: {exc}") from exc


async def import_legacy_session(runtime, session_id: str = "terminal") -> bool:
    """Migrate only the default terminal session; canonical state wins."""
    if session_id != "terminal":
        return False

    async def load() -> list[dict]:
        path = runtime._memory.root / "session_history.json"
        return await asyncio.to_thread(_load_legacy_history, path)

    return await runtime.import_session(session_id, load)


async def run(session_id="terminal", tools=True):
    from .local import load_profile
    load_profile()
    from .orchestrator import get_orchestrator
    from .providers import registry
    tool_set = None
    if tools:
        from . import tui
        tool_set = (tui.TOOLS, tui.execute_tool)
    runtime = await get_orchestrator()
    print(f"HyperClaw — {registry().slot_summary().get('primary')}")
    print("/reset clears this session; /quit exits.")
    try:
        await import_legacy_session(runtime, session_id)
        while True:
            try:
                text = (await asyncio.to_thread(input, "\n> ")).strip()
            except EOFError:
                break
            if text in ("/quit", "/exit", "/q"):
                break
            if text == "/reset":
                await runtime.reset_session(session_id)
                print("Session reset.")
                continue
            if not text:
                continue
            try:
                stream = await runtime.chat(text, session_id, channel="terminal", stream=True,
                                            tools=tools, tool_set=tool_set)
                async for chunk in stream:
                    print(chunk, end="", flush=True)
                print()
            except Exception as exc:
                print(f"\nError: {exc}")
    finally:
        await runtime.shutdown()


def main(session_id="terminal", tools=True):
    try:
        asyncio.run(run(session_id, tools))
    except KeyboardInterrupt:
        print()
