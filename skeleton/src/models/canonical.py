"""Canonical data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import re


def normalize_name(value: str) -> str:
    """Normalize names so later lookup logic has a stable representation."""

    cleaned = re.sub(r"[^a-z0-9\s]", " ", value.lower())
    return " ".join(cleaned.split())


@dataclass(slots=True)
class CaseRecord:
    """Provider-agnostic case shape used after transformation."""

    firm_id: str
    provider: str
    external_case_id: str
    client_name: str
    client_phone: str | None = None
    client_email: str | None = None
    case_status: str | None = None
    assigned_staff: str | None = None
    updated_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_client_name(self) -> str:
        return normalize_name(self.client_name)


@dataclass(slots=True)
class CaseSearchQuery:
    """Placeholder query model for later lookup layer work."""

    firm_id: str
    name: str
    limit: int = 10


@dataclass(slots=True)
class FirmRecord:
    """Tenant-level firm configuration."""

    firm_id: str
    name: str
    provider: str
    provider_credentials: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class StoredSyncState:
    """Provider-aware sync state stored by the repository."""

    firm_id: str
    provider: str
    since: datetime | None = None
    cursor: str | None = None
    page_token: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FieldMappingRecord:
    """Stored mapping override for one canonical field."""

    firm_id: str
    provider: str
    canonical_field: str
    source_fields: list[str]


@dataclass(slots=True)
class SyncResult:
    """Summary returned by the sync engine."""

    firm_id: str
    provider: str
    records_fetched: int
    records_saved: int
    failed_records: int
    success: bool
    partial_failure: bool = False
    error: str | None = None
    is_snapshot: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
