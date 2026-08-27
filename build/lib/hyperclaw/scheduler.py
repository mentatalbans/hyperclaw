"""
HyperClaw Scheduler
APScheduler-based cron system for heartbeat, morning brief, and restart.
"""

import os
import sys
import signal
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from .solomon import get_solomon

load_dotenv()

logger = logging.getLogger("hyperclaw.scheduler")

WORKSPACE_PATH = Path(str(Path.home() / ".hyperclaw/workspace"))
HEARTBEAT_PATH = WORKSPACE_PATH / "HEARTBEAT.md"
ALERT_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))  # the user's Telegram


class HyperClawScheduler:
    """APScheduler-based scheduler for HyperClaw background tasks."""

    def __init__(self, send_telegram_fn: Optional[Callable] = None):
        """
        Initialize the scheduler.

        Args:
            send_telegram_fn: Async function to send Telegram messages
                              Signature: async def send(chat_id: int, text: str) -> bool
        """
        self.scheduler = AsyncIOScheduler(timezone="America/Los_Angeles")
        self.solomon = get_solomon()
        self.send_telegram = send_telegram_fn

    def start(self) -> None:
        """Start the scheduler with all configured jobs."""
        # Heartbeat: every 10 minutes
        self.scheduler.add_job(
            self.heartbeat_job,
            IntervalTrigger(minutes=10),
            id="heartbeat",
            name="Heartbeat Check",
            replace_existing=True,
        )

        # Morning brief: 8:00 AM PST daily
        self.scheduler.add_job(
            self.morning_brief_job,
            CronTrigger(hour=8, minute=0),
            id="morning_brief",
            name="Morning Brief",
            replace_existing=True,
        )

        # Gateway restart: 4:00 AM PST daily
        self.scheduler.add_job(
            self.restart_job,
            CronTrigger(hour=4, minute=0),
            id="gateway_restart",
            name="Gateway Restart",
            replace_existing=True,
        )

        # Sleep consolidation: 2:30 AM PST daily (MemoryStore)
        self.scheduler.add_job(
            self.sleep_consolidation_job,
            CronTrigger(hour=2, minute=30),
            id="sleep_consolidation",
            name="Memory Sleep Consolidation",
            replace_existing=True,
        )

        # All Hands Meeting: 9:00 AM PST daily — all 50 agents report status
        self.scheduler.add_job(
            self.all_hands_job,
            CronTrigger(hour=9, minute=0),
            id="all_hands",
            name="All Hands Meeting",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("Scheduler started with jobs: heartbeat (10min), morning_brief (8AM), all_hands (9AM), restart (4AM), sleep_consolidation (2:30AM)")

    def stop(self) -> None:
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    async def heartbeat_job(self) -> None:
        """
        Heartbeat job — runs every 10 minutes.
        Reads HEARTBEAT.md, sends to SOLOMON, alerts if not OK.
        """
        logger.info("Running heartbeat job...")

        try:
            # Read HEARTBEAT.md
            if not HEARTBEAT_PATH.exists():
                logger.warning("HEARTBEAT.md not found")
                return

            heartbeat_content = HEARTBEAT_PATH.read_text(encoding="utf-8")

            # Build heartbeat prompt
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S PST")
            prompt = (
                f"HEARTBEAT CHECK — {now}\n\n"
                f"Review the following heartbeat instructions and check if anything needs attention.\n"
                f"If everything is OK, respond ONLY with: HEARTBEAT_OK\n"
                f"If there are alerts, issues, or tasks that need attention, describe them briefly.\n\n"
                f"---\n{heartbeat_content}\n---"
            )

            # Get response from SOLOMON
            response = await self.solomon.chat(prompt, [])
            response_stripped = response.strip()

            logger.info(f"Heartbeat response: {response_stripped[:100]}...")

            # Check if OK or alert needed
            if response_stripped == "HEARTBEAT_OK":
                logger.info("Heartbeat OK — no alerts")
            else:
                # Send alert to Telegram
                alert_text = f"HEARTBEAT ALERT\n{now}\n\n{response_stripped}"
                await self._send_alert(alert_text)
                logger.warning(f"Heartbeat alert sent: {response_stripped[:100]}...")

        except Exception as e:
            logger.error(f"Heartbeat job error: {e}", exc_info=True)
            await self._send_alert(f"HEARTBEAT ERROR\n{e}")

    async def morning_brief_job(self) -> None:
        """
        Morning brief job — runs at 8:00 AM PST.
        Asks SOLOMON to generate a morning brief.
        """
        logger.info("Running morning brief job...")

        try:
            now = datetime.now().strftime("%Y-%m-%d")
            prompt = (
                f"Good morning! It's {now}. Please run the morning routine:\n\n"
                f"1. Summarize any overnight items that need attention\n"
                f"2. List today's calendar events or priorities\n"
                f"3. Check trading desk status if applicable\n"
                f"4. Provide a brief daily outlook\n\n"
                f"Keep it concise — this goes directly to the user's Telegram."
            )

            # Get response from SOLOMON
            response = await self.solomon.chat(prompt, [])

            # Send to Telegram
            brief_text = f"MORNING BRIEF — {now}\n\n{response}"
            await self._send_alert(brief_text)
            logger.info("Morning brief sent")

        except Exception as e:
            logger.error(f"Morning brief job error: {e}", exc_info=True)
            await self._send_alert(f"MORNING BRIEF ERROR\n{e}")

    async def all_hands_job(self) -> None:
        """
        All Hands Meeting — 9:00 AM PST daily.
        All key swarm agents report status. Assistant synthesizes and sends to the user.
        """
        logger.info("Running All Hands Meeting...")
        try:
            from hyperclaw.swarm import get_swarm
            swarm = get_swarm()
            result = await swarm.all_hands()

            # Synthesize into a clean brief for the user
            now = datetime.now().strftime("%Y-%m-%d %H:%M PST")
            lines = [f"⚡ ALL HANDS — {now}", ""]

            for report in result.get("reports", []):
                agent_name = report.get("agent_name", "Unknown")
                agent_result = report.get("result", "No report")
                # Trim to 3 lines per agent
                summary = "\n".join(agent_result.split("\n")[:4])
                lines.append(f"**{agent_name}:**")
                lines.append(summary)
                lines.append("")

            brief = "\n".join(lines)
            await self._send_alert(brief[:4000])  # Telegram 4096 char limit
            logger.info("All Hands meeting complete — brief sent to the user")

        except Exception as e:
            logger.error(f"All Hands job error: {e}", exc_info=True)
            await self._send_alert(f"⚠️ ALL HANDS ERROR\n{e}")

    async def sleep_consolidation_job(self) -> None:
        """
        Nightly Memory sleep consolidation — 2:30 AM PST.
        Decays non-core episodes, prunes, generates dream residues.
        Based on the engineering team's memory architecture.
        """
        logger.info("Running Memory sleep consolidation...")
        try:
            import sys
            sys.path.insert(0, str(Path.home() / ".hyperclaw/memory"))
            from memory_store import get_memory_store
            e = get_memory_store()
            result = e.sleep_consolidate()
            logger.info(f"Sleep consolidation: {result}")
            if result.get("skipped"):
                logger.info(f"Skipped: {result.get('reason')}")
            else:
                summary = f"Sleep consolidation: {result['decayed']} decayed, {result['pruned']} pruned, {result['dreams_created']} dreams"
                logger.info(summary)
        except Exception as ex:
            logger.error(f"Sleep consolidation error: {ex}", exc_info=True)

    async def restart_job(self) -> None:
        """
        Gateway restart job — runs at 4:00 AM PST.
        Sends a self-restart signal to refresh the process.
        """
        logger.info("Running gateway restart job...")

        try:
            await self._send_alert("HyperClaw restarting for daily maintenance...")

            # Give time for the message to send
            import asyncio
            await asyncio.sleep(2)

            # Send SIGHUP to self to trigger graceful restart
            # The parent process (launchd) will restart us
            os.kill(os.getpid(), signal.SIGTERM)

        except Exception as e:
            logger.error(f"Restart job error: {e}", exc_info=True)

    async def _send_alert(self, text: str) -> None:
        """Send an alert message to Telegram."""
        if self.send_telegram:
            try:
                await self.send_telegram(ALERT_CHAT_ID, text)
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")
        else:
            logger.warning(f"No Telegram sender configured. Alert: {text[:100]}...")


# Singleton
_scheduler: Optional[HyperClawScheduler] = None


def get_scheduler(send_telegram_fn: Optional[Callable] = None) -> HyperClawScheduler:
    """Get or create the scheduler singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HyperClawScheduler(send_telegram_fn)
    return _scheduler


# ── Sleep Consolidation Job ───────────────────────────────────────────────────

    """Add the nightly Memory sleep consolidation job."""
    scheduler_instance.scheduler.add_job(
        scheduler_instance.sleep_consolidation_job,
        CronTrigger(hour=2, minute=30),
        id="sleep_consolidation",
        name="Memory Sleep Consolidation",
        replace_existing=True,
    )
