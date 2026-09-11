"""Thin scheduling boundary over Store's atomic reservation and enqueue."""
from datetime import datetime

from hyperclaw.contracts import InvalidRequest, schedule_instant


class Scheduler:
    def __init__(self, store):
        self.store = store

    async def tick(self, now: datetime) -> list[str]:
        try:
            normalized = schedule_instant(now)
        except (ValueError, TypeError, AttributeError):
            raise InvalidRequest() from None
        return await self.store.tick_schedules(normalized)
