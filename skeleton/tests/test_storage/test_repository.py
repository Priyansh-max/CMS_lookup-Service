from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from src.models.canonical import (
    CaseRecord,
    CaseSearchQuery,
    FieldMappingRecord,
    FirmIntegrationRecord,
    FirmRecord,
    StoredSyncState,
)
from src.storage.database import Base
from src.storage.repository import CaseRepositoryImpl


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


def _save_firm(repository: CaseRepositoryImpl, firm_id: str) -> None:
    asyncio.run(
        repository.save_firm(
            FirmRecord(
                firm_id=firm_id,
                name=f"Firm {firm_id}",
            )
        )
    )


def _save_integration(
    repository: CaseRepositoryImpl,
    firm_id: str,
    provider: str,
    credentials: dict[str, str] | None = None,
) -> None:
    asyncio.run(
        repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id=firm_id,
                provider=provider,
                provider_credentials=credentials or {},
            )
        )
    )


def test_repository_saves_and_lists_firms() -> None:
    repository = asyncio.run(_create_clean_repository())

    _save_firm(repository, "firm-1")
    _save_firm(repository, "firm-2")

    firms = asyncio.run(repository.list_firms())
    stored = asyncio.run(repository.get_firm("firm-1"))

    assert [firm.firm_id for firm in firms] == ["firm-1", "firm-2"]
    assert stored is not None
    assert stored.name == "Firm firm-1"

    asyncio.run(repository.close())


def test_repository_saves_and_lists_integrations() -> None:
    repository = asyncio.run(_create_clean_repository())
    _save_firm(repository, "firm-1")

    _save_integration(repository, "firm-1", "clio", {"access_token": "token-1"})
    _save_integration(repository, "firm-1", "filevine", {"sample_path": "/tmp/sample.json"})

    stored = asyncio.run(repository.get_firm_integration("firm-1", "clio"))
    integrations = asyncio.run(repository.list_firm_integrations("firm-1"))

    assert stored is not None
    assert stored.provider_credentials["access_token"] == "token-1"
    assert [integration.provider for integration in integrations] == ["clio", "filevine"]

    asyncio.run(repository.close())


def test_repository_saves_and_updates_cases() -> None:
    repository = asyncio.run(_create_clean_repository())
    _save_firm(repository, "firm-1")

    first = CaseRecord(
        firm_id="firm-1",
        provider="clio",
        external_case_id="case-1",
        client_name="John Smith",
        case_status="open",
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    updated = CaseRecord(
        firm_id="firm-1",
        provider="clio",
        external_case_id="case-1",
        client_name="John Smith",
        case_status="closed",
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    asyncio.run(repository.save_case(first))
    asyncio.run(repository.save_case(updated))
    stored = asyncio.run(repository.get_case_by_external_id("firm-1", "clio", "case-1"))

    assert stored is not None
    assert stored.case_status == "closed"
    assert stored.updated_at == datetime(2024, 1, 2, tzinfo=timezone.utc)

    asyncio.run(repository.close())


def test_repository_find_candidates_is_tenant_scoped() -> None:
    repository = asyncio.run(_create_clean_repository())
    _save_firm(repository, "firm-1")
    _save_firm(repository, "firm-2")

    asyncio.run(
        repository.save_cases(
            [
                CaseRecord(
                    firm_id="firm-1",
                    provider="clio",
                    external_case_id="case-1",
                    client_name="John Smith",
                ),
                CaseRecord(
                    firm_id="firm-2",
                    provider="clio",
                    external_case_id="case-2",
                    client_name="John Smith",
                ),
            ]
        )
    )

    results = asyncio.run(
        repository.find_candidates_by_name(CaseSearchQuery(firm_id="firm-1", name="john smith"))
    )

    assert len(results) == 1
    assert results[0].firm_id == "firm-1"

    asyncio.run(repository.close())


def test_repository_sync_state_round_trip() -> None:
    repository = asyncio.run(_create_clean_repository())
    _save_firm(repository, "firm-1")

    state = StoredSyncState(
        firm_id="firm-1",
        provider="filevine",
        cursor="cursor-1",
        metadata={"strategy": "snapshot"},
    )

    asyncio.run(repository.upsert_sync_state(state))
    loaded = asyncio.run(repository.get_sync_state("firm-1", "filevine"))

    assert loaded is not None
    assert loaded.cursor == "cursor-1"
    assert loaded.metadata["strategy"] == "snapshot"

    asyncio.run(repository.close())


def test_repository_saves_and_replaces_field_mappings() -> None:
    repository = asyncio.run(_create_clean_repository())
    _save_firm(repository, "firm-1")

    asyncio.run(
        repository.save_field_mappings(
            [
                FieldMappingRecord(
                    firm_id="firm-1",
                    provider="filevine",
                    canonical_field="client_name",
                    source_fields=["contact.full_name"],
                )
            ]
        )
    )

    asyncio.run(
        repository.save_field_mappings(
            [
                FieldMappingRecord(
                    firm_id="firm-1",
                    provider="filevine",
                    canonical_field="client_name",
                    source_fields=["contact.first_name", "contact.last_name"],
                )
            ]
        )
    )

    mappings = asyncio.run(repository.get_field_mappings("firm-1", "filevine"))

    assert mappings == {"client_name": ["contact.first_name", "contact.last_name"]}

    asyncio.run(repository.close())
