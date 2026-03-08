from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

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
