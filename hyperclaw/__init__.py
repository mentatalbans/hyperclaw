"""
HyperClaw — Multi-Agent AI Orchestration Platform
"""

__version__ = "1.0.7"

# Core imports (always available)
try:
    from .solomon import Solomon, get_solomon
except ImportError:
    Solomon, get_solomon = None, None

try:
    from .agent import HyperClawAgent, get_agent
except ImportError:
    HyperClawAgent, get_agent = None, None

try:
    from .scheduler import HyperClawScheduler, get_scheduler
except ImportError:
    HyperClawScheduler, get_scheduler = None, None

# Optional imports (require extra dependencies)
try:
    from .telegram_bot import TelegramBot, get_telegram_bot
except ImportError:
    TelegramBot, get_telegram_bot = None, None

__all__ = [
    "Solomon",
    "get_solomon",
    "HyperClawAgent",
    "get_agent",
    "TelegramBot",
    "get_telegram_bot",
    "HyperClawScheduler",
    "get_scheduler",
]
