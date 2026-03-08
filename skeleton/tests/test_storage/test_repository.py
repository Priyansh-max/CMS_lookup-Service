from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.models.canonical import CaseRecord, CaseSearchQuery, FieldMappingRecord, StoredSyncState
from src.storage.repository import CaseRepositoryImpl


def _database_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


def test_repository_saves_and_updates_cases(tmp_path) -> None:
    repository = CaseRepositoryImpl(_database_url(tmp_path))
    asyncio.run(repository.initialize())

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


def test_repository_find_candidates_is_tenant_scoped(tmp_path) -> None:
    repository = CaseRepositoryImpl(_database_url(tmp_path))
    asyncio.run(repository.initialize())

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


def test_repository_sync_state_round_trip(tmp_path) -> None:
    repository = CaseRepositoryImpl(_database_url(tmp_path))
    asyncio.run(repository.initialize())

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


def test_repository_saves_and_replaces_field_mappings(tmp_path) -> None:
    repository = CaseRepositoryImpl(_database_url(tmp_path))
    asyncio.run(repository.initialize())

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
