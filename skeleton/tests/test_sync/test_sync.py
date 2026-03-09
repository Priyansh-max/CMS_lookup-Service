from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from src.models.canonical import CaseRecord, FirmIntegrationRecord, FirmRecord
from src.providers.base import ProviderSyncResult
from src.providers.clio import ClioProvider
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
    integration = asyncio.run(repository.get_firm_integration("firm-1", "filevine"))
    sync_state = asyncio.run(repository.get_sync_state("firm-1", "filevine"))

    assert result.success is True
    assert result.records_fetched == 1
    assert result.records_saved == 1
    assert stored is not None
    assert integration is not None
    assert integration.provider_credentials["sample_path"] == str(sample_file)
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


def test_sync_engine_refreshes_clio_credentials_from_integration() -> None:
    repository = asyncio.run(_create_clean_repository())
    asyncio.run(
        repository.save_firm(
            FirmRecord(
                firm_id="firm-1",
                name="Firm One",
            )
        )
    )
    asyncio.run(
        repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id="firm-1",
                provider="clio",
                provider_credentials={
                    "access_token": "expired-token",
                    "refresh_token": "refresh-token",
                    "token_expires_at": (
                        datetime.now(tz=timezone.utc) - timedelta(minutes=5)
                    ).isoformat(),
                },
            )
        )
    )

    provider = ClioProvider(client_id="client-id", client_secret="client-secret")

    async def fake_refresh(credentials):
        assert credentials["refresh_token"] == "refresh-token"
        refreshed = dict(credentials)
        refreshed["access_token"] = "fresh-token"
        refreshed["token_expires_at"] = (
            datetime.now(tz=timezone.utc) + timedelta(hours=1)
        ).isoformat()
        return refreshed

    async def fake_sync_cases(*, firm_id, credentials, sync_state=None):
        assert firm_id == "firm-1"
        assert credentials["access_token"] == "fresh-token"
        return ProviderSyncResult(
            records=[{"id": "matter-1", "client": {"name": "Jane Doe"}, "updated_at": "2024-01-01T00:00:00Z"}],
            next_state=None,
            is_snapshot=False,
        )

    provider.refresh_access_token = fake_refresh  # type: ignore[method-assign]
    provider.sync_cases = fake_sync_cases  # type: ignore[method-assign]

    class DummyTransformer:
        def transform(self, raw_record, *, firm_id, mapping_overrides):
            return CaseRecord(
                firm_id=firm_id,
                provider="clio",
                external_case_id=raw_record["id"],
                client_name=raw_record["client"]["name"],
            )

    engine = SyncEngine(
        repository=repository,
        providers={"clio": provider},
        transformers={"clio": DummyTransformer()},  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.sync_provider(SyncRequest(firm_id="firm-1", provider="clio")))
    updated_integration = asyncio.run(repository.get_firm_integration("firm-1", "clio"))

    assert result.success is True
    assert updated_integration is not None
    assert updated_integration.provider_credentials["access_token"] == "fresh-token"

    asyncio.run(repository.close())


def test_sync_engine_refreshes_filevine_live_credentials_from_pat() -> None:
    repository = asyncio.run(_create_clean_repository())
    asyncio.run(
        repository.save_firm(
            FirmRecord(
                firm_id="firm-1",
                name="Firm One",
            )
        )
    )
    asyncio.run(
        repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id="firm-1",
                provider="filevine",
                provider_credentials={
                    "pat": "filevine-pat",
                },
            )
        )
    )

    provider = FilevineProvider(client_id="client-id", client_secret="client-secret")

    async def fake_refresh(credentials):
        assert credentials["pat"] == "filevine-pat"
        refreshed = dict(credentials)
        refreshed["access_token"] = "live-access-token"
        refreshed["user_id"] = "123"
        refreshed["org_id"] = "456"
        refreshed["token_expires_at"] = (
            datetime.now(tz=timezone.utc) + timedelta(hours=1)
        ).isoformat()
        return refreshed

    async def fake_sync_cases(*, firm_id, credentials, sync_state=None):
        assert firm_id == "firm-1"
        assert credentials["access_token"] == "live-access-token"
        assert credentials["user_id"] == "123"
        assert credentials["org_id"] == "456"
        return ProviderSyncResult(
            records=[
                {
                    "project": {
                        "project_id": "project-1",
                        "phase": "intake",
                        "last_activity_at": "2024-01-01T00:00:00Z",
                    },
                    "contact": {"full_name": "Jane Doe"},
                }
            ],
            next_state=None,
            is_snapshot=False,
        )

    provider.refresh_access_token = fake_refresh  # type: ignore[method-assign]
    provider.sync_cases = fake_sync_cases  # type: ignore[method-assign]

    engine = SyncEngine(
        repository=repository,
        providers={"filevine": provider},
        transformers={"filevine": FilevineTransformer()},
    )

    result = asyncio.run(engine.sync_provider(SyncRequest(firm_id="firm-1", provider="filevine")))
    updated_integration = asyncio.run(repository.get_firm_integration("firm-1", "filevine"))

    assert result.success is True
    assert updated_integration is not None
    assert updated_integration.provider_credentials["access_token"] == "live-access-token"
    assert updated_integration.provider_credentials["org_id"] == "456"

    asyncio.run(repository.close())


def test_sync_scheduler_start_and_stop(monkeypatch) -> None:
    class DummyEngine:
        async def sync_provider(self, request):
            return None

    class DummyRepository:
        async def list_firms(self):
            return []

        async def list_firm_integrations(self, firm_id):
            return []

    scheduler = SyncScheduler(
        sync_engine=DummyEngine(),  # type: ignore[arg-type]
        repository=DummyRepository(),  # type: ignore[arg-type]
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


def test_sync_scheduler_discovers_active_integrations_from_repository() -> None:
    class DummyRepository:
        async def list_firms(self):
            return [
                FirmRecord(firm_id="firm-1", name="Firm One", is_active=True),
                FirmRecord(firm_id="firm-2", name="Firm Two", is_active=False),
            ]

        async def list_firm_integrations(self, firm_id):
            if firm_id == "firm-1":
                return [
                    FirmIntegrationRecord(
                        firm_id="firm-1",
                        provider="filevine",
                        provider_credentials={"sample_path": "demo.json"},
                        is_active=True,
                        auto_sync_enabled=True,
                    ),
                    FirmIntegrationRecord(
                        firm_id="firm-1",
                        provider="clio",
                        provider_credentials={"access_token": "token"},
                        is_active=True,
                        auto_sync_enabled=False,
                    ),
                    FirmIntegrationRecord(
                        firm_id="firm-1",
                        provider="hubspot",
                        provider_credentials={"token": "ignored"},
                        is_active=False,
                        auto_sync_enabled=True,
                    ),
                ]
            return [
                FirmIntegrationRecord(
                    firm_id=firm_id,
                    provider="filevine",
                    provider_credentials={"sample_path": "ignored.json"},
                    is_active=True,
                    auto_sync_enabled=True,
                )
            ]

    class DummyEngine:
        async def sync_provider(self, request):
            return None

    scheduler = SyncScheduler(
        sync_engine=DummyEngine(),  # type: ignore[arg-type]
        repository=DummyRepository(),  # type: ignore[arg-type]
        requests=[],
        interval_seconds=10,
    )

    requests = asyncio.run(scheduler.refresh_requests())

    assert len(requests) == 1
    assert requests[0].firm_id == "firm-1"
    assert requests[0].provider == "filevine"
