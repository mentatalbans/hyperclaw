"""
HyperClaw — Multi-Agent AI Orchestration Platform

A production-ready AI assistant platform with:
- Multi-agent orchestration
- Persistent memory across sessions
- Integration with Telegram, Email, Calendar, and more
- Database-backed state management
"""

__version__ = "1.0.9"

# Core components
try:
    from .orchestrator import Orchestrator, get_orchestrator
except ImportError:
    Orchestrator, get_orchestrator = None, None

try:
    from .memory_manager import MemoryManager, get_memory_manager
except ImportError:
    MemoryManager, get_memory_manager = None, None

try:
    from .setup import run_setup, setup_sync
except ImportError:
    run_setup, setup_sync = None, None

# Legacy imports (backwards compatibility)
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

try:
    from .telegram_bot import TelegramBot, get_telegram_bot
except ImportError:
    TelegramBot, get_telegram_bot = None, None

__all__ = [
    # Core
    "Orchestrator",
    "get_orchestrator",
    "MemoryManager",
    "get_memory_manager",
    "run_setup",
    "setup_sync",
    # Legacy
    "Solomon",
    "get_solomon",
    "HyperClawAgent",
    "get_agent",
    "TelegramBot",
    "get_telegram_bot",
    "HyperClawScheduler",
    "get_scheduler",
]
