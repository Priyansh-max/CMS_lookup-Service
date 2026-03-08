from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.providers.base import (
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderSyncState,
    ProviderTemporaryError,
)
from src.providers.clio import ClioProvider
from src.providers.filevine import FilevineProvider


def test_clio_provider_requires_access_token() -> None:
    provider = ClioProvider(api_base_url="https://example.test")

    with pytest.raises(ProviderConfigurationError):
        asyncio.run(provider.sync_cases(firm_id="firm-1", credentials={}))


def test_clio_provider_builds_incremental_sync_result(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClioProvider(api_base_url="https://example.test")

    responses = [
        {
            "data": [{"id": "1", "updated_at": "2024-01-01T00:00:00Z"}],
            "meta": {"paging": {"next": "page-2"}},
        },
        {
            "data": [{"id": "2", "updated_at": "2024-01-03T00:00:00Z"}],
            "meta": {"paging": {}},
        },
    ]

    class DummyResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            self.calls: list[dict] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict, headers: dict) -> DummyResponse:
            self.calls.append({"url": url, "params": dict(params), "headers": dict(headers)})
            return DummyResponse(responses.pop(0))

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    result = asyncio.run(
        provider.sync_cases(
            firm_id="firm-1",
            credentials={"access_token": "token"},
            sync_state=ProviderSyncState(
                since=datetime(2024, 1, 1, tzinfo=timezone.utc)
            ),
        )
    )

    assert len(result.records) == 2
    assert result.is_snapshot is False
    assert result.next_state is not None
    assert result.next_state.metadata["strategy"] == "timestamp"
    assert result.next_state.since == datetime(2024, 1, 3, tzinfo=timezone.utc)


def test_clio_provider_raises_payload_error_for_invalid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClioProvider(api_base_url="https://example.test")

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"unexpected": []}

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict, headers: dict) -> DummyResponse:
            return DummyResponse()

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    with pytest.raises(ProviderPayloadError):
        asyncio.run(
            provider.sync_cases(
                firm_id="firm-1",
                credentials={"access_token": "token"},
            )
        )


def test_clio_provider_converts_timeout_to_temporary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClioProvider(api_base_url="https://example.test")

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict, headers: dict):
            raise httpx.TimeoutException("boom")

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    with pytest.raises(ProviderTemporaryError):
        asyncio.run(
            provider.sync_cases(
                firm_id="firm-1",
                credentials={"access_token": "token"},
            )
        )


def test_clio_provider_marks_expired_credentials_for_refresh() -> None:
    provider = ClioProvider(
        api_base_url="https://example.test",
        client_id="client-id",
        client_secret="client-secret",
    )

    needs_refresh = provider.credentials_need_refresh(
        {
            "access_token": "token",
            "refresh_token": "refresh-token",
            "token_expires_at": (
                datetime.now(tz=timezone.utc) - timedelta(minutes=5)
            ).isoformat(),
        }
    )

    assert needs_refresh is True


def test_clio_provider_refreshes_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClioProvider(
        api_base_url="https://example.test",
        oauth_token_url="https://example.test/oauth/token",
        client_id="client-id",
        client_secret="client-secret",
    )

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, data: dict, headers: dict) -> DummyResponse:
            assert data["grant_type"] == "refresh_token"
            assert data["refresh_token"] == "old-refresh-token"
            return DummyResponse()

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    refreshed = asyncio.run(
        provider.refresh_access_token(
            {
                "access_token": "old-access-token",
                "refresh_token": "old-refresh-token",
            }
        )
    )

    assert refreshed["access_token"] == "new-access-token"
    assert refreshed["refresh_token"] == "new-refresh-token"
    assert "token_expires_at" in refreshed


def test_clio_provider_builds_authorize_url() -> None:
    provider = ClioProvider(
        client_id="client-id",
        redirect_uri="http://127.0.0.1/oauth/callback",
        scopes=["matters:read", "contacts:read"],
    )

    url = provider.build_authorize_url(state='{"firm_id":"firm-1","provider":"clio"}')

    assert "response_type=code" in url
    assert "client_id=client-id" in url
    assert "redirect_uri=" in url
    assert "state=" in url
    assert "scope=" in url


def test_filevine_provider_reads_snapshot_records(tmp_path) -> None:
    sample_file = tmp_path / "filevine.json"
    sample_file.write_text(
        json.dumps({"records": [{"id": "a"}, {"id": "b"}]}),
        encoding="utf-8",
    )

    provider = FilevineProvider()
    result = asyncio.run(
        provider.sync_cases(
            firm_id="firm-2",
            credentials={"sample_path": str(sample_file)},
        )
    )

    assert result.is_snapshot is True
    assert len(result.records) == 2
    assert result.next_state is not None
    assert result.next_state.metadata["strategy"] == "snapshot"
    assert result.next_state.metadata["record_count"] == 2


def test_filevine_provider_exchanges_pat_for_live_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FilevineProvider(
        client_id="filevine-client-id",
        client_secret="filevine-client-secret",
        identity_url="https://identity.example.test/connect/token",
        org_lookup_url="https://api.example.test/fv-app/v2/utils/GetUserOrgsWithToken",
    )

    class DummyResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, data: dict | None = None, headers: dict | None = None):
            if "identity" in url:
                assert data is not None
                assert data["grant_type"] == "personal_access_token"
                assert data["token"] == "filevine-pat"
                return DummyResponse(
                    {
                        "access_token": "live-access-token",
                        "expires_in": 3600,
                        "scope": "fv.api.gateway.access tenant",
                    }
                )
            return DummyResponse(
                {
                    "data": [
                        {
                            "userId": 123,
                            "orgId": 456,
                        }
                    ]
                }
            )

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    refreshed = asyncio.run(
        provider.refresh_access_token(
            {
                "pat": "filevine-pat",
            }
        )
    )

    assert refreshed["access_token"] == "live-access-token"
    assert refreshed["user_id"] == "123"
    assert refreshed["org_id"] == "456"
    assert "token_expires_at" in refreshed


def test_filevine_provider_reads_live_records(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FilevineProvider(
        projects_url="https://api.example.test/fv-app/v2/Projects",
    )

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "projects": [
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

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict | None = None):
            assert headers is not None
            assert headers["Authorization"] == "Bearer live-access-token"
            assert headers["x-fv-orgid"] == "456"
            assert headers["x-fv-userid"] == "123"
            return DummyResponse()

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    result = asyncio.run(
        provider.sync_cases(
            firm_id="firm-2",
            credentials={
                "access_token": "live-access-token",
                "user_id": "123",
                "org_id": "456",
            },
        )
    )

    assert result.is_snapshot is False
    assert len(result.records) == 1
    assert result.next_state is not None
    assert result.next_state.metadata["strategy"] == "live-pat"


def test_filevine_provider_requires_sample_path() -> None:
    provider = FilevineProvider()

    with pytest.raises(ProviderConfigurationError):
        asyncio.run(provider.sync_cases(firm_id="firm-2", credentials={}))


def test_filevine_provider_rejects_invalid_json(tmp_path) -> None:
    sample_file = tmp_path / "filevine.json"
    sample_file.write_text("{not-json", encoding="utf-8")

    provider = FilevineProvider()

    with pytest.raises(ProviderPayloadError):
        asyncio.run(
            provider.sync_cases(
                firm_id="firm-2",
                credentials={"sample_path": str(sample_file)},
            )
        )
