import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.filevine import FilevineProvider
from src.storage.repository import CaseRepositoryImpl
from src.sync.engine import SyncEngine, SyncRequest
from src.transformers.filevine_transformer import FilevineTransformer


async def main():
    db_path = PROJECT_ROOT / "manual_sync.db"
    sample_path = Path(__file__).with_name("filevine_sample.json")

    repo = CaseRepositoryImpl(f"sqlite+aiosqlite:///{db_path}")
    await repo.initialize()

    engine = SyncEngine(
        repository=repo,
        providers={
            "filevine": FilevineProvider()
        },
        transformers={
            "filevine": FilevineTransformer()
        },
    )

    request = SyncRequest(
        firm_id="firm-1",
        provider="filevine",
        credentials={"sample_path": str(sample_path)},
    )

    print("\n--- Running sync engine ---")
    result = await engine.sync_provider(request)
    print(result)

    print("\n--- Reading stored case ---")
    stored_case = await repo.get_case_by_external_id(
        "firm-1",
        "filevine",
        "project-1001",
    )
    print(stored_case)

    print("\n--- Reading stored sync state ---")
    stored_sync_state = await repo.get_sync_state("firm-1", "filevine")
    print(stored_sync_state)

    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())