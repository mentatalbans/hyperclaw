#!/usr/bin/env python3
"""
Assistant Background Agents — Autonomous monitoring and alerting.
Runs continuously, watches email/markets/news, alerts the user.
"""

import os
import sys
import json
import time
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

HYPERCLAW_ROOT = Path(__file__).parent.parent
AGENTS_STATE_FILE = HYPERCLAW_ROOT / "agents" / "state.json"
ALERTS_LOG = HYPERCLAW_ROOT / "logs" / "alerts.log"

# Ensure directories exist
AGENTS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)


def load_env():
    """Load environment from .env file."""
    env_file = HYPERCLAW_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"'))


load_env()


class BackgroundAgent:
    """Base class for background monitoring agents."""

    def __init__(self, name: str, interval: int = 300):
        self.name = name
        self.interval = interval  # seconds
        self.running = False
        self.last_run = None
        self.last_result = None
        self.error_count = 0

    async def check(self) -> Optional[Dict]:
        """Override this method to implement monitoring logic.
        Return dict with 'alert' key if attention needed."""
        raise NotImplementedError

    async def run_once(self) -> Optional[Dict]:
        """Run a single check."""
        try:
            result = await self.check()
            self.last_run = datetime.now()
            self.last_result = result
            self.error_count = 0
            return result
        except Exception as e:
            self.error_count += 1
            return {"error": str(e)}

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "interval": self.interval,
            "running": self.running,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "error_count": self.error_count
        }


class EmailMonitor(BackgroundAgent):
    """Monitor email for important messages."""

    def __init__(self, keywords: List[str] = None, senders: List[str] = None):
        super().__init__("email_monitor", interval=300)  # 5 min
        self.keywords = keywords or ["urgent", "asap", "important", "deadline"]
        self.senders = senders or []
        self.seen_ids = set()

    async def check(self) -> Optional[Dict]:
        # Use AppleScript to check Mail.app
        import subprocess
        script = '''
        tell application "Mail"
            set output to ""
            set unreadMsgs to (messages of inbox whose read status is false)
            repeat with m in unreadMsgs
                set output to output & (id of m) & "|" & (sender of m) & "|" & (subject of m) & "\\n"
            end repeat
            return output
        end tell
        '''
        try:
            result = subprocess.run(["osascript", "-e", script],
                capture_output=True, text=True, timeout=30)

            alerts = []
            for line in result.stdout.strip().split('\n'):
                if not line or '|' not in line:
                    continue
                parts = line.split('|', 2)
                if len(parts) < 3:
                    continue
                msg_id, sender, subject = parts

                if msg_id in self.seen_ids:
                    continue
                self.seen_ids.add(msg_id)

                # Check for important emails
                is_important = False
                reason = ""

                # Check senders
                for s in self.senders:
                    if s.lower() in sender.lower():
                        is_important = True
                        reason = f"From {s}"
                        break

                # Check keywords
                if not is_important:
                    for kw in self.keywords:
                        if kw.lower() in subject.lower():
                            is_important = True
                            reason = f"Contains '{kw}'"
                            break

                if is_important:
                    alerts.append({
                        "type": "email",
                        "sender": sender,
                        "subject": subject,
                        "reason": reason
                    })

            if alerts:
                return {"alert": True, "emails": alerts}
            return {"alert": False, "checked": len(self.seen_ids)}

        except Exception as e:
            return {"error": str(e)}


class MarketMonitor(BackgroundAgent):
    """Monitor market prices and alert on significant moves."""

    def __init__(self, symbols: List[str] = None, threshold: float = 0.05):
        super().__init__("market_monitor", interval=60)  # 1 min
        self.symbols = symbols or ["BTC-USD", "ETH-USD", "SPY", "AAPL"]
        self.threshold = threshold  # 5% move
        self.last_prices = {}

    async def check(self) -> Optional[Dict]:
        alerts = []

        async with httpx.AsyncClient(timeout=10) as client:
            for symbol in self.symbols:
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    resp = await client.get(url)
                    data = resp.json()

                    price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    prev_close = data["chart"]["result"][0]["meta"].get("previousClose", price)

                    change_pct = (price - prev_close) / prev_close if prev_close else 0

                    # Check against last known price for sudden moves
                    if symbol in self.last_prices:
                        last = self.last_prices[symbol]
                        move = abs((price - last) / last)
                        if move >= self.threshold:
                            direction = "up" if price > last else "down"
                            alerts.append({
                                "type": "market",
                                "symbol": symbol,
                                "price": price,
                                "move": f"{move*100:.1f}% {direction}",
                                "prev": last
                            })

                    self.last_prices[symbol] = price

                except Exception as e:
                    continue

        if alerts:
            return {"alert": True, "moves": alerts}
        return {"alert": False, "prices": self.last_prices}


class NewsMonitor(BackgroundAgent):
    """Monitor news for relevant topics."""

    def __init__(self, topics: List[str] = None):
        super().__init__("news_monitor", interval=900)  # 15 min
        self.topics = topics or ["Organization", "AI hospitality", "hotel technology"]
        self.seen_titles = set()

    async def check(self) -> Optional[Dict]:
        alerts = []

        async with httpx.AsyncClient(timeout=15) as client:
            for topic in self.topics:
                try:
                    url = f"https://news.google.com/rss/search?q={topic.replace(' ', '+')}"
                    resp = await client.get(url)

                    import re
                    titles = re.findall(r'<title>(.+?)</title>', resp.text)[1:6]

                    for title in titles:
                        if title not in self.seen_titles:
                            self.seen_titles.add(title)
                            alerts.append({
                                "type": "news",
                                "topic": topic,
                                "title": title
                            })
                except:
                    continue

        if alerts:
            return {"alert": True, "news": alerts[:5]}  # Max 5 alerts
        return {"alert": False, "topics_checked": len(self.topics)}


class AgentManager:
    """Manages all background agents."""

    def __init__(self):
        self.agents: Dict[str, BackgroundAgent] = {}
        self.running = False
        self.alert_callback: Optional[Callable] = None
        self._task = None

    def register(self, agent: BackgroundAgent):
        """Register an agent."""
        self.agents[agent.name] = agent

    def set_alert_callback(self, callback: Callable):
        """Set callback for alerts (e.g., send Telegram)."""
        self.alert_callback = callback

    async def _run_loop(self):
        """Main monitoring loop."""
        while self.running:
            for name, agent in self.agents.items():
                if not agent.running:
                    continue

                # Check if it's time to run
                if agent.last_run:
                    elapsed = (datetime.now() - agent.last_run).total_seconds()
                    if elapsed < agent.interval:
                        continue

                # Run check
                result = await agent.run_once()

                # Handle alerts
                if result and result.get("alert"):
                    await self._handle_alert(agent, result)

            await asyncio.sleep(10)  # Check every 10 seconds

    async def _handle_alert(self, agent: BackgroundAgent, result: Dict):
        """Handle an alert from an agent."""
        timestamp = datetime.now().isoformat()
        alert_msg = f"[{timestamp}] {agent.name}: {json.dumps(result)}"

        # Log alert
        with open(ALERTS_LOG, "a") as f:
            f.write(alert_msg + "\n")

        # Call callback (e.g., Telegram)
        if self.alert_callback:
            try:
                await self.alert_callback(agent.name, result)
            except:
                pass

    def start(self):
        """Start all agents."""
        self.running = True
        for agent in self.agents.values():
            agent.running = True

        # Run in background
        loop = asyncio.new_event_loop()
        self._task = threading.Thread(target=lambda: loop.run_until_complete(self._run_loop()))
        self._task.daemon = True
        self._task.start()

    def stop(self):
        """Stop all agents."""
        self.running = False
        for agent in self.agents.values():
            agent.running = False

    def status(self) -> Dict:
        """Get status of all agents."""
        return {
            "running": self.running,
            "agents": {name: agent.to_dict() for name, agent in self.agents.items()}
        }

    def save_state(self):
        """Save agent state to disk."""
        state = {
            "running": self.running,
            "agents": {}
        }
        for name, agent in self.agents.items():
            state["agents"][name] = {
                "running": agent.running,
                "interval": agent.interval,
                "last_run": agent.last_run.isoformat() if agent.last_run else None
            }
        AGENTS_STATE_FILE.write_text(json.dumps(state, indent=2))

    def load_state(self):
        """Load agent state from disk."""
        if AGENTS_STATE_FILE.exists():
            try:
                state = json.loads(AGENTS_STATE_FILE.read_text())
                for name, data in state.get("agents", {}).items():
                    if name in self.agents:
                        self.agents[name].running = data.get("running", False)
                        self.agents[name].interval = data.get("interval", 300)
            except:
                pass


# Telegram alert sender
async def send_telegram_alert(agent_name: str, result: Dict):
    """Send alert via Telegram."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token:
        return

    # Format message
    if agent_name == "email_monitor" and result.get("emails"):
        emails = result["emails"]
        msg = f"📧 *{len(emails)} Important Email(s)*\n\n"
        for e in emails[:3]:
            msg += f"From: {e['sender']}\nSubject: {e['subject']}\nReason: {e['reason']}\n\n"

    elif agent_name == "market_monitor" and result.get("moves"):
        moves = result["moves"]
        msg = f"📈 *Market Alert*\n\n"
        for m in moves:
            emoji = "🟢" if "up" in m["move"] else "🔴"
            msg += f"{emoji} {m['symbol']}: ${m['price']:.2f} ({m['move']})\n"

    elif agent_name == "news_monitor" and result.get("news"):
        news = result["news"]
        msg = f"📰 *News Alert*\n\n"
        for n in news[:3]:
            msg += f"• {n['title'][:100]}\n"

    else:
        msg = f"🔔 *{agent_name}*\n{json.dumps(result, indent=2)[:500]}"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "Markdown"
        })


# Global manager instance
manager = AgentManager()

# Register default agents
manager.register(EmailMonitor(
    keywords=["urgent", "asap", "important", "investor", "funding", "deal"],
    senders=["andreessen", "sequoia", "investor", "ceo"]
))
manager.register(MarketMonitor(
    symbols=["BTC-USD", "ETH-USD", "SPY", "NVDA"],
    threshold=0.03  # 3% moves
))
manager.register(NewsMonitor(
    topics=["Organization", "AI hospitality", "hotel technology", "AI news"]
))
manager.set_alert_callback(send_telegram_alert)


# CLI functions for TUI integration
def agent_start(name: str = None) -> str:
    """Start an agent or all agents."""
    if name:
        if name in manager.agents:
            manager.agents[name].running = True
            if not manager.running:
                manager.start()
            return f"Started {name}"
        return f"Unknown agent: {name}"
    else:
        manager.start()
        return f"Started all agents: {list(manager.agents.keys())}"


def agent_stop(name: str = None) -> str:
    """Stop an agent or all agents."""
    if name:
        if name in manager.agents:
            manager.agents[name].running = False
            return f"Stopped {name}"
        return f"Unknown agent: {name}"
    else:
        manager.stop()
        return "Stopped all agents"


def agent_status() -> Dict:
    """Get agent status."""
    return manager.status()


def agent_add_watch(agent_type: str, target: str) -> str:
    """Add a watch target to an agent."""
    if agent_type == "email" and "email_monitor" in manager.agents:
        agent = manager.agents["email_monitor"]
        if "@" in target:
            agent.senders.append(target)
        else:
            agent.keywords.append(target)
        return f"Added '{target}' to email monitor"

    elif agent_type == "market" and "market_monitor" in manager.agents:
        agent = manager.agents["market_monitor"]
        agent.symbols.append(target.upper())
        return f"Added {target.upper()} to market monitor"

    elif agent_type == "news" and "news_monitor" in manager.agents:
        agent = manager.agents["news_monitor"]
        agent.topics.append(target)
        return f"Added '{target}' to news monitor"

    return f"Unknown agent type: {agent_type}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: background_monitor.py <start|stop|status>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start":
        print(agent_start())
        # Keep running
        try:
            while True:
                time.sleep(60)
                manager.save_state()
        except KeyboardInterrupt:
            manager.stop()
            print("\nStopped")

    elif cmd == "stop":
        print(agent_stop())

    elif cmd == "status":
        print(json.dumps(agent_status(), indent=2))

    else:
        print(f"Unknown command: {cmd}")
