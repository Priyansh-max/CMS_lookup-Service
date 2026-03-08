import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models.canonical import (
    CaseRecord,
    CaseSearchQuery,
    FieldMappingRecord,
    StoredSyncState,
)
from src.storage.repository import CaseRepositoryImpl


async def main():
    db_path = PROJECT_ROOT / "manual_testing.db"
    repo = CaseRepositoryImpl(f"sqlite+aiosqlite:///{db_path}")
    await repo.initialize()

    case = CaseRecord(
        firm_id="firm-1",
        provider="filevine",
        external_case_id="project-1001",
        client_name="Jon Smyth",
        client_phone="111-222-3333",
        client_email="jon.smyth@example.com",
        case_status="intake",
        assigned_staff="Alex Lawyer",
        updated_at=datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc),
    )

    print("\n--- Saving case ---")
    await repo.save_case(case)

    print("\n--- Reading case by external id ---")
    stored_case = await repo.get_case_by_external_id("firm-1", "filevine", "project-1001")
    print(stored_case)

    print("\n--- Saving sync state ---")
    sync_state = StoredSyncState(
        firm_id="firm-1",
        provider="filevine",
        since=None,
        cursor=None,
        page_token=None,
        metadata={"strategy": "snapshot", "record_count": 3},
    )
    await repo.upsert_sync_state(sync_state)

    print("\n--- Reading sync state ---")
    loaded_sync_state = await repo.get_sync_state("firm-1", "filevine")
    print(loaded_sync_state)

    print("\n--- Saving field mappings ---")
    mappings = [
        FieldMappingRecord(
            firm_id="firm-1",
            provider="filevine",
            canonical_field="client_name",
            source_fields=["contact.first_name", "contact.last_name"],
        )
    ]
    await repo.save_field_mappings(mappings)

    print("\n--- Reading field mappings ---")
    loaded_mappings = await repo.get_field_mappings("firm-1", "filevine")
    print(loaded_mappings)

    print("\n--- Searching by name ---")
    candidates = await repo.find_candidates_by_name(
        CaseSearchQuery(firm_id="firm-1", name="jon smyth")
    )
    print(candidates)

    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())