"""Filevine CMS Provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.providers.base import (
    CaseManagementProvider,
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderSyncResult,
    ProviderSyncState,
)


class FilevineProvider(CaseManagementProvider):
    """Load snapshot-style raw case payloads for Filevine."""

    def __init__(self, *, default_sample_path: str | None = None):
        self.default_sample_path = default_sample_path

    @property
    def provider_name(self) -> str:
        return "filevine"

    async def sync_cases(
        self,
        *,
        firm_id: str,
        credentials: dict[str, Any],
        sync_state: ProviderSyncState | None = None,
    ) -> ProviderSyncResult:
        sample_path = credentials.get("sample_path") or self.default_sample_path
        if not sample_path:
            raise ProviderConfigurationError(
                f"Missing Filevine sample path for firm {firm_id}"
            )

        path = Path(sample_path)
        if not path.exists():
            raise ProviderConfigurationError(
                f"Filevine sample file does not exist: {path}"
            )

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderPayloadError("Filevine sample file contains invalid JSON") from exc

        records = self._extract_records(payload)

        # Filevine is modeled as snapshot ingestion for now, so we do not pretend
        # that provider-side updated_at filtering is always available.
        next_state = ProviderSyncState(
            since=sync_state.since if sync_state else None,
            metadata={
                "strategy": "snapshot",
                "record_count": len(records),
                "source_path": str(path),
            },
        )
        return ProviderSyncResult(records=records, next_state=next_state, is_snapshot=True)

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            for key in ("data", "records", "projects"):
                value = payload.get(key)
                if isinstance(value, list):
                    records = value
                    break
            else:
                raise ProviderPayloadError(
                    "Filevine payload must contain a top-level list of records"
                )
        else:
            raise ProviderPayloadError("Unsupported Filevine payload shape")

        if not all(isinstance(record, dict) for record in records):
            raise ProviderPayloadError("Filevine records must be objects")

        return records
