"""
HyperClaw CLI Entry Point
Usage: python -m hyperclaw <command>
"""

import argparse
import asyncio
import sys
import os

from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="hyperclaw",
        description="HyperClaw AI Assistant Platform"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Initialize HyperClaw")
    setup_parser.add_argument("--init-db", action="store_true", help="Initialize database schema")
    setup_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    setup_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    # Server command
    server_parser = subparsers.add_parser("server", help="Run the API server")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    server_parser.add_argument("--port", type=int, default=8001, help="Port to bind to")
    server_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Interactive chat mode")
    chat_parser.add_argument("--session", default="cli", help="Session ID")

    # TUI command (full terminal UI)
    tui_parser = subparsers.add_parser("tui", help="Full terminal UI with tools")
    tui_parser.add_argument("--session", default="tui", help="Session ID")

    # Status command
    subparsers.add_parser("status", help="Show system status")

    # Memory commands
    memory_parser = subparsers.add_parser("memory", help="Memory operations")
    memory_sub = memory_parser.add_subparsers(dest="memory_cmd")
    memory_sub.add_parser("list", help="List recent memories")
    recall_parser = memory_sub.add_parser("recall", help="Recall memories")
    recall_parser.add_argument("query", help="Search query")
    remember_parser = memory_sub.add_parser("remember", help="Store a memory")
    remember_parser.add_argument("content", help="Content to remember")

    # Version
    subparsers.add_parser("version", help="Show version")

    args = parser.parse_args()

    if args.command == "setup":
        from hyperclaw.setup import setup_sync
        result = setup_sync(
            init_db=args.init_db,
            overwrite=args.overwrite,
            verbose=args.verbose
        )
        sys.exit(0 if result.success else 1)

    elif args.command == "server":
        os.environ["HOST"] = args.host
        os.environ["PORT"] = str(args.port)
        if args.reload:
            os.environ["RELOAD"] = "true"

        from hyperclaw.server import main as run_server
        run_server()

    elif args.command == "chat":
        asyncio.run(interactive_chat(args.session))

    elif args.command == "tui":
        from hyperclaw.cli_tui import main as tui_main
        tui_main()

    elif args.command == "status":
        asyncio.run(show_status())

    elif args.command == "memory":
        asyncio.run(handle_memory(args))

    elif args.command == "version":
        print("HyperClaw v1.0.0")

    else:
        parser.print_help()


async def interactive_chat(session_id: str):
    """Interactive chat mode with TUI."""
    try:
        # Try the enhanced TUI first
        from hyperclaw.cli_tui import main as tui_main
        tui_main()
        return
    except ImportError:
        pass

    # Fallback to basic chat
    from hyperclaw.orchestrator import get_orchestrator

    print("\n" + "=" * 60)
    print("  HyperClaw Interactive Chat")
    print("  Type 'quit' to exit, 'clear' to reset session")
    print("=" * 60 + "\n")

    orchestrator = await get_orchestrator()

    while True:
        try:
            user_input = input("\nYou: ").strip()

            if not user_input:
                continue

            if user_input.lower() == "quit":
                print("\nGoodbye!")
                break

            if user_input.lower() == "clear":
                if orchestrator._memory:
                    orchestrator._memory._conversation_history.pop(session_id, None)
                print("Session cleared.")
                continue

            # Get response
            print("\nAssistant: ", end="", flush=True)

            response = await orchestrator.chat(
                message=user_input,
                session_id=session_id,
                channel="cli"
            )

            print(response)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


async def show_status():
    """Show system status."""
    from hyperclaw.orchestrator import get_orchestrator

    try:
        orchestrator = await get_orchestrator()
        status = orchestrator.get_status()

        print("\n" + "=" * 60)
        print("  HyperClaw Status")
        print("=" * 60)

        print(f"\n  Initialized: {status['initialized']}")
        print(f"  Model: {status['model']}")
        print(f"  Database: {status['database']}")

        print("\n  Integrations:")
        for name, state in status['integrations'].items():
            print(f"    - {name}: {state}")

        print("\n  Memory:")
        for key, val in status['memory'].items():
            print(f"    - {key}: {val}")

        print(f"\n  Timestamp: {status['timestamp']}")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"Error getting status: {e}")


async def handle_memory(args):
    """Handle memory commands."""
    from hyperclaw.orchestrator import get_orchestrator

    orchestrator = await get_orchestrator()

    if args.memory_cmd == "recall":
        memories = await orchestrator.recall(args.query)
        print(f"\nFound {len(memories)} memories:\n")
        for mem in memories:
            print(f"  [{mem.memory_type}] {mem.content[:100]}...")
            print()

    elif args.memory_cmd == "remember":
        memory_id = await orchestrator.remember(args.content)
        print(f"Stored memory: {memory_id}")

    elif args.memory_cmd == "list":
        if orchestrator._memory:
            for mtype, memories in orchestrator._memory._file_cache.items():
                print(f"\n{mtype.upper()} ({len(memories)}):")
                for mem in memories[:5]:
                    print(f"  - {mem.content[:80]}...")

    else:
        print("Usage: hyperclaw memory [list|recall|remember]")


if __name__ == "__main__":
    main()
