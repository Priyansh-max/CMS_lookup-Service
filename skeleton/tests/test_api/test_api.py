from __future__ import annotations

import asyncio
import json
import os

from fastapi.testclient import TestClient

from src.main import create_app
from src.storage.database import Base
from src.storage.repository import CaseRepositoryImpl


def _test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TEST_DATABASE_URL is required for automated tests")
    return database_url


async def _reset_test_database() -> None:
    repository = CaseRepositoryImpl(_test_database_url())
    async with repository.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await repository.close()


def test_health_endpoint(monkeypatch) -> None:
    asyncio.run(_reset_test_database())
    monkeypatch.setenv("DATABASE_URL", _test_database_url())
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_start_and_callback_for_clio(monkeypatch) -> None:
    asyncio.run(_reset_test_database())
    monkeypatch.setenv("DATABASE_URL", _test_database_url())
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("CLIO_CLIENT_ID", "client-id")
    monkeypatch.setenv("CLIO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("CLIO_REDIRECT_URI", "http://127.0.0.1/oauth/callback")

    async def fake_exchange(self, code: str) -> dict:
        assert code == "auth-code"
        return {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setattr("src.providers.clio.ClioProvider.exchange_code_for_token", fake_exchange)

    app = create_app()
    with TestClient(app) as client:
        firm_response = client.post(
            "/firms",
            json={
                "firm_id": "firm-1",
                "name": "Firm One",
            },
        )
        assert firm_response.status_code == 200

        start_response = client.get("/auth/clio/start", params={"firm_id": "firm-1"})
        assert start_response.status_code == 200
        state = start_response.json()["state"]
        assert "authorization_url" in start_response.json()

        callback_response = client.get(
            "/auth/clio/callback",
            params={
                "code": "auth-code",
                "state": state,
            },
        )
        assert callback_response.status_code == 200
        assert callback_response.json()["connected"] is True
        assert callback_response.json()["has_refresh_token"] is True

        integrations_response = client.get("/firms/firm-1/integrations")
        assert integrations_response.status_code == 200
        assert integrations_response.json()[0]["provider"] == "clio"


def test_sync_lookup_and_mapping_end_to_end(monkeypatch, tmp_path) -> None:
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
                        "contact": {
                            "first_name": "Jon",
                            "last_name": "Smyth",
                            "mobile_phone": "111-222-3333",
                            "email": "jon@example.com",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    asyncio.run(_reset_test_database())
    monkeypatch.setenv("DATABASE_URL", _test_database_url())
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    app = create_app()
    with TestClient(app) as client:
        firm_response = client.post(
            "/firms",
            json={
                "firm_id": "firm-1",
                "name": "Firm One",
            },
        )
        assert firm_response.status_code == 200

        integration_response = client.post(
            "/firms/firm-1/integrations",
            json={
                "provider": "filevine",
                "provider_credentials": {"sample_path": str(sample_file)},
            },
        )
        assert integration_response.status_code == 200

        sync_response = client.post(
            "/sync",
            json={
                "requests": [
                    {
                        "firm_id": "firm-1",
                        "provider": "filevine",
                    }
                ]
            },
        )
        assert sync_response.status_code == 200
        assert sync_response.json()[0]["records_saved"] == 1

        integrations_response = client.get("/firms/firm-1/integrations")
        assert integrations_response.status_code == 200
        assert integrations_response.json()[0]["provider"] == "filevine"

        lookup_response = client.get(
            "/cases/lookup",
            params={"firm_id": "firm-1", "name": "John Smith"},
        )
        assert lookup_response.status_code == 200
        lookup_results = lookup_response.json()
        assert len(lookup_results) == 1
        assert lookup_results[0]["client_name"] == "Jon Smyth"
        assert lookup_results[0]["match_type"] == "fuzzy"

        mapping_response = client.post(
            "/firms/firm-1/mapping",
            json={
                "provider": "filevine",
                "mappings": {"client_name": ["contact.full_name"]},
            },
        )
        assert mapping_response.status_code == 200
        assert mapping_response.json()["saved_mappings"] == 1

def test_sync_endpoint_requires_requests_when_no_defaults(monkeypatch) -> None:
    asyncio.run(_reset_test_database())
    monkeypatch.setenv("DATABASE_URL", _test_database_url())
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.delenv("FILEVINE_SAMPLE_PATH", raising=False)

    app = create_app()
    with TestClient(app) as client:
        response = client.post("/sync")

    assert response.status_code == 400
    assert "No sync requests" in response.json()["detail"]
