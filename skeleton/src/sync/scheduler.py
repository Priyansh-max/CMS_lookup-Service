"""Sync Scheduler."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.storage.base import CaseRepository
from src.sync.engine import SyncEngine, SyncRequest


class SyncScheduler:
    """Thin APScheduler wrapper for periodically running sync jobs."""

    def __init__(
        self,
        *,
        sync_engine: SyncEngine,
        repository: CaseRepository,
        requests: list[SyncRequest] | None = None,
        interval_seconds: int = 300,
    ):
        self.sync_engine = sync_engine
        self.repository = repository
        self.seed_requests = requests or []
        self.requests: list[SyncRequest] = []
        self.interval_seconds = interval_seconds
        self.scheduler = AsyncIOScheduler()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        await self.refresh_schedule()
        self.scheduler.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False

    async def refresh_requests(self) -> list[SyncRequest]:
        requests_by_key: dict[tuple[str, str], SyncRequest] = {
            (request.firm_id, request.provider): request for request in self.seed_requests
        }

        for firm in await self.repository.list_firms():
            if not firm.is_active:
                continue

            integrations = await self.repository.list_firm_integrations(firm.firm_id)
            for integration in integrations:
                if not integration.is_active or not integration.auto_sync_enabled:
                    continue
                requests_by_key[(integration.firm_id, integration.provider)] = SyncRequest(
                    firm_id=integration.firm_id,
                    provider=integration.provider,
                    firm_name=firm.name,
                )

        self.requests = sorted(
            requests_by_key.values(),
            key=lambda request: (request.firm_id, request.provider),
        )
        return list(self.requests)

    async def refresh_schedule(self) -> list[SyncRequest]:
        requests = await self.refresh_requests()

        if self._started:
            self.scheduler.remove_all_jobs()

        for request in requests:
            self.scheduler.add_job(
                self._run_sync_job,
                trigger=IntervalTrigger(seconds=self.interval_seconds),
                id=f"sync:{request.firm_id}:{request.provider}",
                replace_existing=True,
                kwargs={"request": request},
            )

        return requests

    async def _run_sync_job(self, request: SyncRequest) -> None:
        await self.sync_engine.sync_provider(request)
