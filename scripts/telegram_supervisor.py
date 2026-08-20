#!/usr/bin/env python3
"""
Telegram Supervisor — Ensures Telegram bot stays running permanently.

Features:
- Kills any conflicting processes on startup
- Monitors the bot process
- Auto-restarts on crash with exponential backoff
- Prevents duplicate instances
- Clears webhook and locks before each start
- Health reporting for watchdog integration

Run as: python telegram_supervisor.py
Or install as LaunchAgent for auto-start on boot.
"""

import os
import sys
import time
import signal
import subprocess
import json
import logging
import fcntl
import httpx
from pathlib import Path
from datetime import datetime

# Setup
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
LOG_DIR = HYPERCLAW_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_SCRIPT = HYPERCLAW_ROOT / "scripts" / "telegram_direct.py"
LOCK_FILE = Path("/tmp/telegram_supervisor.lock")
BOT_LOCK_FILE = Path("/tmp/hyperclaw_telegram.lock")
HEALTH_FILE = HYPERCLAW_ROOT / "data" / "telegram_health.json"
HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)

# Load env
from dotenv import load_dotenv
load_dotenv(HYPERCLAW_ROOT / ".env")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SUPERVISOR] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "telegram_supervisor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("telegram_supervisor")


class TelegramSupervisor:
    """Supervises the Telegram bot process."""

    MAX_RESTARTS_PER_HOUR = 10
    INITIAL_BACKOFF = 5
    MAX_BACKOFF = 300

    def __init__(self):
        self.running = True
        self.process = None
        self.restart_count = 0
        self.restart_times = []
        self.backoff = self.INITIAL_BACKOFF
        self.start_time = datetime.now()
        self.lock_file = None

        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

    def _shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        self._stop_bot()
        self._release_lock()
        self._update_health("stopped")
        sys.exit(0)

    def _acquire_lock(self) -> bool:
        """Ensure only one supervisor runs."""
        try:
            self.lock_file = open(LOCK_FILE, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(str(os.getpid()))
            self.lock_file.flush()
            return True
        except (IOError, OSError):
            logger.error("Another supervisor instance is already running")
            return False

    def _release_lock(self):
        """Release the lock file."""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    def _update_health(self, status: str, error: str = None):
        """Write health status for watchdog."""
        try:
            health = {
                "daemon": "telegram",
                "status": status,
                "last_heartbeat": datetime.now().isoformat(),
                "uptime_seconds": (datetime.now() - self.start_time).total_seconds(),
                "restart_count": self.restart_count,
                "backoff_seconds": self.backoff,
                "bot_pid": self.process.pid if self.process else None,
                "error": error
            }
            HEALTH_FILE.write_text(json.dumps(health, indent=2))
        except Exception:
            pass

    def _kill_existing(self):
        """Kill all existing Telegram bot processes."""
        logger.info("Killing any existing Telegram processes...")
        patterns = [
            "telegram_direct.py",
            "telegram_bot.py",
            "run_telegram.py",
        ]
        for pattern in patterns:
            subprocess.run(["pkill", "-9", "-f", pattern], capture_output=True)
        time.sleep(1)

    def _clear_locks(self):
        """Clear all Telegram lock files."""
        lock_files = [
            BOT_LOCK_FILE,
            Path("/tmp/telegram_direct.lock"),
            HYPERCLAW_ROOT / "data" / "telegram.lock",
        ]
        for lf in lock_files:
            try:
                lf.unlink(missing_ok=True)
            except Exception:
                pass

    def _clear_webhook(self):
        """Delete any existing webhook."""
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{API_BASE}/deleteWebhook",
                    params={"drop_pending_updates": "true"}
                )
                if response.status_code == 200:
                    logger.info("Webhook cleared")
                else:
                    logger.warning(f"Webhook clear failed: {response.status_code}")
        except Exception as e:
            logger.error(f"Webhook clear error: {e}")

    def _start_bot(self) -> bool:
        """Start the Telegram bot process."""
        try:
            # Redirect the bot's stdout/stderr to a log file, NOT a PIPE. With stdout=PIPE
            # and nothing draining it, the bot deadlocks the moment its output exceeds the
            # OS pipe buffer (~16KB on macOS): it blocks on a write() and silently stops
            # polling Telegram while the process still appears alive (the exact "bot went
            # quiet on Telegram" symptom). The bot keeps its own FileHandler
            # (telegram_direct.log); this just safely captures anything it writes to stdout.
            bot_log = open(LOG_DIR / "telegram_bot_stdout.log", "ab", buffering=0)
            self.process = subprocess.Popen(
                [sys.executable, str(TELEGRAM_SCRIPT)],
                stdout=bot_log,
                stderr=subprocess.STDOUT,
                cwd=str(HYPERCLAW_ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"}
            )
            logger.info(f"Started Telegram bot (PID: {self.process.pid})")
            return True
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            return False

    def _stop_bot(self):
        """Stop the bot process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception:
                pass
            self.process = None

    def _check_bot(self) -> bool:
        """Check if bot is running."""
        if self.process is None:
            return False
        poll = self.process.poll()
        return poll is None  # None means still running

    def _should_restart(self) -> bool:
        """Check if we should restart (rate limiting)."""
        now = time.time()
        # Remove restarts older than 1 hour
        self.restart_times = [t for t in self.restart_times if now - t < 3600]

        if len(self.restart_times) >= self.MAX_RESTARTS_PER_HOUR:
            logger.error(f"Rate limited: {len(self.restart_times)} restarts in last hour")
            return False
        return True

    def run(self):
        """Main supervisor loop."""
        if not self._acquire_lock():
            return

        logger.info("Telegram Supervisor starting...")
        self._update_health("starting")

        # Initial cleanup
        self._kill_existing()
        self._clear_locks()
        self._clear_webhook()
        time.sleep(2)

        # Start bot
        if not self._start_bot():
            logger.error("Initial bot start failed")
            self._release_lock()
            return

        self._update_health("running")

        # Monitor loop
        while self.running:
            try:
                if not self._check_bot():
                    # Bot died
                    exit_code = self.process.returncode if self.process else -1
                    logger.warning(f"Bot process died (exit code: {exit_code})")

                    if not self._should_restart():
                        logger.error("Too many restarts, backing off significantly")
                        self.backoff = min(self.backoff * 2, self.MAX_BACKOFF)
                        self._update_health("rate_limited")
                        time.sleep(self.backoff)
                        continue

                    # Restart with backoff
                    logger.info(f"Restarting in {self.backoff}s...")
                    self._update_health("restarting")
                    time.sleep(self.backoff)

                    self._clear_locks()
                    self._clear_webhook()
                    time.sleep(1)

                    if self._start_bot():
                        self.restart_count += 1
                        self.restart_times.append(time.time())
                        # Exponential backoff, reset on successful run
                        self.backoff = min(self.backoff * 1.5, self.MAX_BACKOFF)
                        self._update_health("running")
                    else:
                        self.backoff = min(self.backoff * 2, self.MAX_BACKOFF)
                        self._update_health("failed_to_start")

                else:
                    # Bot is running, reset backoff gradually
                    if self.backoff > self.INITIAL_BACKOFF:
                        self.backoff = max(self.backoff * 0.9, self.INITIAL_BACKOFF)
                    self._update_health("running")

                time.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Supervisor error: {e}")
                time.sleep(30)

        self._stop_bot()
        self._release_lock()
        logger.info("Supervisor shutdown complete")


if __name__ == "__main__":
    supervisor = TelegramSupervisor()
    supervisor.run()
