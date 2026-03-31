#!/usr/bin/env python3
"""
HyperClaw — Main Entry Point
Starts FastAPI server, Telegram bot, and APScheduler together.
"""

import os
import sys
import signal
import asyncio
import logging
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

# ── Logging Setup ─────────────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "hyperclaw.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("hyperclaw")

# ── Globals ───────────────────────────────────────────────────────────────────
shutdown_event = asyncio.Event()


def run_fastapi_server():
    """Run the FastAPI server in a separate thread."""
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("HYPERCLAW_PORT", "8001"))

    logger.info(f"Starting FastAPI server on {host}:{port}")

    config = uvicorn.Config(
        "server:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)

    # Run in non-blocking mode for thread
    asyncio.run(server.serve())


async def run_telegram_bot(bot):
    """Run the Telegram bot polling loop."""
    logger.info("Starting Telegram bot...")

    application = bot.build()

    # Initialize and start
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    logger.info("Telegram bot started — polling for messages")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Cleanup
    logger.info("Stopping Telegram bot...")
    await application.updater.stop()
    await application.stop()
    await application.shutdown()


async def main():
    """Main async entry point."""
    logger.info("=" * 60)
    logger.info("HyperClaw starting...")
    logger.info("=" * 60)

    # Import here to avoid circular imports
    from hyperclaw.telegram_bot import get_telegram_bot
    from hyperclaw.scheduler import get_scheduler

    # Get telegram bot instance
    bot = get_telegram_bot()

    # Create scheduler with telegram send function
    async def send_telegram(chat_id: int, text: str) -> bool:
        return await bot.send_message(chat_id, text)

    scheduler = get_scheduler(send_telegram)

    # Start FastAPI server in a thread
    server_thread = threading.Thread(target=run_fastapi_server, daemon=True)
    server_thread.start()
    logger.info("FastAPI server thread started")

    # Start scheduler
    scheduler.start()

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Run the Telegram bot (blocks until shutdown)
        await run_telegram_bot(bot)
    except Exception as e:
        logger.error(f"Error in main loop: {e}", exc_info=True)
    finally:
        # Cleanup
        scheduler.stop()
        logger.info("HyperClaw shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
