from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(dotenv_path: Path) -> None:
    """Load a very small .env file without adding another dependency."""

    if not dotenv_path.exists():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def print_usage() -> None:
    print(
        "Usage:\n"
        "  python skeleton/src/manual_testing/clio_auth.py authorize\n"
        "  python skeleton/src/manual_testing/clio_auth.py exchange <authorization_code>\n"
        "  python skeleton/src/manual_testing/clio_auth.py refresh <refresh_token>\n"
    )


def build_authorize_url() -> str:
    client_id = require_env("CLIO_CLIENT_ID")
    redirect_uri = require_env("CLIO_REDIRECT_URI")
    return (
        "https://app.clio.com/oauth/authorize"
        f"?response_type=code&client_id={quote(client_id)}&redirect_uri={quote(redirect_uri)}"
    )


def exchange_code_for_token(code: str) -> dict:
    client_id = require_env("CLIO_CLIENT_ID")
    client_secret = require_env("CLIO_CLIENT_SECRET")
    redirect_uri = require_env("CLIO_REDIRECT_URI")

    response = httpx.post(
        "https://app.clio.com/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    client_id = require_env("CLIO_CLIENT_ID")
    client_secret = require_env("CLIO_CLIENT_SECRET")

    response = httpx.post(
        "https://app.clio.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def build_integration_credentials(token_response: dict, existing_refresh_token: str | None = None) -> dict:
    expires_in = token_response.get("expires_in")
    token_expires_at = None
    if isinstance(expires_in, (int, float)):
        token_expires_at = (
            datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))
        ).isoformat()

    credentials = {
        "access_token": token_response.get("access_token"),
        "refresh_token": token_response.get("refresh_token") or existing_refresh_token,
        "token_type": token_response.get("token_type", "Bearer"),
    }
    if "scope" in token_response:
        credentials["scope"] = token_response["scope"]
    if token_expires_at:
        credentials["token_expires_at"] = token_expires_at
    return credentials


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "authorize":
        print("Open this URL in your browser and approve the app:\n")
        print(build_authorize_url())
        print(
            "\nAfter approval, Clio will redirect to your redirect URI with "
            "a ?code=... query parameter."
        )
        return

    if command == "exchange":
        if len(sys.argv) < 3:
            print("Missing authorization code.\n")
            print_usage()
            sys.exit(1)

        token_response = exchange_code_for_token(sys.argv[2])
        print("Token response:\n")
        print(json.dumps(token_response, indent=2))

        print("\nUse this as the integration credentials payload:\n")
        print(json.dumps(build_integration_credentials(token_response), indent=2))
        return

    if command == "refresh":
        if len(sys.argv) < 3:
            print("Missing refresh token.\n")
            print_usage()
            sys.exit(1)

        token_response = refresh_access_token(sys.argv[2])
        print("Refresh response:\n")
        print(json.dumps(token_response, indent=2))
        print("\nUpdated integration credentials:\n")
        print(json.dumps(build_integration_credentials(token_response, sys.argv[2]), indent=2))
        return

    print(f"Unknown command: {command}\n")
    print_usage()
    sys.exit(1)


if __name__ == "__main__":
    main()
