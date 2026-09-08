"""Public imports loaded on demand, after entrypoints configure the environment."""
from importlib import import_module

__version__ = "1.2.0"
_EXPORTS = {
    "Orchestrator": "orchestrator", "get_orchestrator": "orchestrator",
    "MemoryManager": "memory_manager", "get_memory_manager": "memory_manager",
    "run_setup": "setup", "setup_sync": "setup",
    "Solomon": "solomon", "get_solomon": "solomon",
    "HyperClawAgent": "agent", "get_agent": "agent",
    "TelegramBot": "telegram_bot", "get_telegram_bot": "telegram_bot",
    "HyperClawScheduler": "scheduler", "get_scheduler": "scheduler",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(f".{_EXPORTS[name]}", __name__), name)
    globals()[name] = value
    return value
