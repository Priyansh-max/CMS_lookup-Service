"""Sync Scheduler."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.sync.engine import SyncEngine, SyncRequest


class SyncScheduler:
    """Thin APScheduler wrapper for periodically running sync jobs."""

    def __init__(
        self,
        *,
        sync_engine: SyncEngine,
        requests: list[SyncRequest],
        interval_seconds: int = 300,
    ):
        self.sync_engine = sync_engine
        self.requests = requests
        self.interval_seconds = interval_seconds
        self.scheduler = AsyncIOScheduler()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        for request in self.requests:
            self.scheduler.add_job(
                self._run_sync_job,
                trigger=IntervalTrigger(seconds=self.interval_seconds),
                id=f"sync:{request.firm_id}:{request.provider}",
                replace_existing=True,
                kwargs={"request": request},
            )

        self.scheduler.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False

    async def _run_sync_job(self, request: SyncRequest) -> None:
        await self.sync_engine.sync_provider(request)
