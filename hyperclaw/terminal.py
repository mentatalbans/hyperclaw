"""Terminal adapter for the canonical conversation runtime."""
import asyncio


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
