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
provider = ClioProvider(
    client_id=os.getenv("CLIO_CLIENT_ID"),
    client_secret=os.getenv("CLIO_CLIENT_SECRET"),
)
access_token = os.getenv("CLIO_ACCESS_TOKEN")
refresh_token = os.getenv("CLIO_REFRESH_TOKEN")
token_expires_at = os.getenv("CLIO_TOKEN_EXPIRES_AT")

if not access_token and not refresh_token:
    raise RuntimeError(
        "Set CLIO_ACCESS_TOKEN or CLIO_REFRESH_TOKEN in your .env before running this script."
    )

credentials = {
    key: value
    for key, value in {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": token_expires_at,
    }.items()
    if value
}

if provider.credentials_need_refresh(credentials):
    credentials = asyncio.run(provider.refresh_access_token(credentials))
    print("Refreshed credentials metadata:")
    print(
        {
            "has_access_token": bool(credentials.get("access_token")),
            "has_refresh_token": bool(credentials.get("refresh_token")),
            "token_expires_at": credentials.get("token_expires_at"),
        }
    )

result = asyncio.run(provider.sync_cases(firm_id="firm-1", credentials=credentials))

print(result)