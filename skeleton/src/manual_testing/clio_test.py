import asyncio
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.clio import ClioProvider


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line or line.startswith("export "):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv(PROJECT_ROOT / ".env")
provider = ClioProvider()
access_token = os.getenv("CLIO_ACCESS_TOKEN")

if not access_token:
    raise RuntimeError("Set CLIO_ACCESS_TOKEN in your .env before running this script.")

result = asyncio.run(
    provider.sync_cases(
        firm_id="firm-1",
        credentials={"access_token": access_token}
    )
)

print(result)