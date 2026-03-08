"""Clio CMS Provider."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from src.providers.base import (
    CaseManagementProvider,
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderSyncResult,
    ProviderSyncState,
    ProviderTemporaryError,
)


class ClioProvider(CaseManagementProvider):
    """Fetch raw case payloads from the Clio API."""

    def __init__(
        self,
        *,
        api_base_url: str = "https://app.clio.com/api/v4",
        timeout_seconds: float = 15.0,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "clio"

    async def sync_cases(
        self,
        *,
        firm_id: str,
        credentials: dict[str, Any],
        sync_state: ProviderSyncState | None = None,
    ) -> ProviderSyncResult:
        access_token = credentials.get("access_token")
        if not access_token:
            raise ProviderConfigurationError(
                f"Missing Clio access token for firm {firm_id}"
            )

        params = self._build_query_params(sync_state)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        records: list[dict[str, Any]] = []
        next_page_token = sync_state.page_token if sync_state else None

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                while True:
                    if next_page_token:
                        params["page_token"] = next_page_token
                    response = await client.get(
                        f"{self.api_base_url}/matters",
                        params=params,
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload = response.json()

                    page_records, next_page_token = self._parse_response(payload)
                    records.extend(page_records)

                    if not next_page_token:
                        break
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while calling Clio") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                raise ProviderConfigurationError(
                    f"Clio rejected credentials for firm {firm_id}"
                ) from exc
            raise ProviderTemporaryError(
                f"Clio request failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Clio request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Clio returned invalid JSON") from exc

        return ProviderSyncResult(
            records=records,
            next_state=ProviderSyncState(
                since=self._latest_updated_at(records, sync_state),
                metadata={"strategy": "timestamp"},
            ),
            is_snapshot=False,
        )

    def _build_query_params(
        self,
        sync_state: ProviderSyncState | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 200}
        if sync_state and sync_state.since:
            params["updated_since"] = sync_state.since.isoformat()
        return params

    def _parse_response(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not isinstance(payload, dict):
            raise ProviderPayloadError("Clio response must be an object")

        raw_records = payload.get("data")
        if raw_records is None or not isinstance(raw_records, list):
            raise ProviderPayloadError("Clio response missing data list")

        next_page_token = None
        meta = payload.get("meta")
        if isinstance(meta, dict):
            paging = meta.get("paging")
            if isinstance(paging, dict):
                next_page_token = paging.get("next")

        return raw_records, next_page_token

    def _latest_updated_at(
        self,
        records: list[dict[str, Any]],
        sync_state: ProviderSyncState | None,
    ) -> datetime | None:
        latest_seen = sync_state.since if sync_state else None
        for record in records:
            updated_at = record.get("updated_at")
            if not isinstance(updated_at, str):
                continue
            try:
                parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if latest_seen is None or parsed > latest_seen:
                latest_seen = parsed
        return latest_seen
