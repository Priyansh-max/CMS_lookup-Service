from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from src.models.canonical import StoredSyncState
from src.providers.filevine import FilevineProvider
from src.storage.database import Base
from src.storage.repository import CaseRepositoryImpl
from src.sync.engine import SyncEngine, SyncRequest
from src.sync.scheduler import SyncScheduler
from src.transformers.filevine_transformer import FilevineTransformer


def _test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TEST_DATABASE_URL is required for automated tests")
    return database_url


async def _create_clean_repository() -> CaseRepositoryImpl:
    repository = CaseRepositoryImpl(_test_database_url())
    async with repository.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return repository


def test_sync_engine_runs_snapshot_provider_end_to_end(tmp_path) -> None:
    sample_file = tmp_path / "filevine.json"
    sample_file.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "project": {
                            "project_id": "project-1",
                            "phase": "intake",
                            "last_activity_at": "2024-01-01T00:00:00Z",
                        },
                        "contact": {"full_name": "Jane Doe"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    repository = asyncio.run(_create_clean_repository())
    engine = SyncEngine(
        repository=repository,
        providers={"filevine": FilevineProvider()},
        transformers={"filevine": FilevineTransformer()},
    )

    result = asyncio.run(
        engine.sync_provider(
            SyncRequest(
                firm_id="firm-1",
                provider="filevine",
                credentials={"sample_path": str(sample_file)},
            )
        )
    )

    stored = asyncio.run(repository.get_case_by_external_id("firm-1", "filevine", "project-1"))
    sync_state = asyncio.run(repository.get_sync_state("firm-1", "filevine"))

    assert result.success is True
    assert result.records_fetched == 1
    assert result.records_saved == 1
    assert stored is not None
    assert sync_state is not None
    assert sync_state.metadata["strategy"] == "snapshot"

    asyncio.run(repository.close())


def test_sync_engine_reports_partial_failure_for_bad_record(tmp_path) -> None:
    sample_file = tmp_path / "filevine_bad.json"
    sample_file.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "project": {
                            "project_id": "project-1",
                            "phase": "intake",
                            "last_activity_at": "2024-01-01T00:00:00Z",
                        },
                        "contact": {"full_name": "Good Record"},
                    },
                    {
                        "project": {
                            "project_id": "project-2",
                            "last_activity_at": "2024-01-01T00:00:00Z",
                        },
                        "contact": {},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    repository = asyncio.run(_create_clean_repository())
    engine = SyncEngine(
        repository=repository,
        providers={"filevine": FilevineProvider()},
        transformers={"filevine": FilevineTransformer()},
    )

    result = asyncio.run(
        engine.sync_provider(
            SyncRequest(
                firm_id="firm-1",
                provider="filevine",
                credentials={"sample_path": str(sample_file)},
            )
        )
    )

    assert result.success is False
    assert result.partial_failure is True
    assert result.failed_records == 1
    assert result.records_saved == 1

    asyncio.run(repository.close())


def test_sync_scheduler_start_and_stop(monkeypatch) -> None:
    class DummyEngine:
        async def sync_provider(self, request):
            return None

    scheduler = SyncScheduler(
        sync_engine=DummyEngine(),  # type: ignore[arg-type]
        requests=[],
        interval_seconds=10,
    )

    started = {"value": False}
    stopped = {"value": False}

    monkeypatch.setattr(scheduler.scheduler, "start", lambda: started.__setitem__("value", True))
    monkeypatch.setattr(
        scheduler.scheduler,
        "shutdown",
        lambda wait=False: stopped.__setitem__("value", True),
    )

    asyncio.run(scheduler.start())
    asyncio.run(scheduler.stop())

    assert started["value"] is True
    assert stopped["value"] is True
