from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.main import create_app


def test_health_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api_full.db'}")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    app = create_app()
    with TestClient(app) as client:
        sync_response = client.post(
            "/sync",
            json={
                "requests": [
                    {
                        "firm_id": "firm-1",
                        "provider": "filevine",
                        "credentials": {"sample_path": str(sample_file)},
                    }
                ]
            },
        )
        assert sync_response.status_code == 200
        assert sync_response.json()[0]["records_saved"] == 1

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


def test_sync_endpoint_requires_requests_when_no_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api_empty.db'}")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.delenv("CLIO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("FILEVINE_SAMPLE_PATH", raising=False)

    app = create_app()
    with TestClient(app) as client:
        response = client.post("/sync")

    assert response.status_code == 400
    assert "No sync requests" in response.json()["detail"]
