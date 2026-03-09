"""Canonical data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import re
import unicodedata


def normalize_name(value: str) -> str:
    """Normalize names so later lookup logic has a stable representation."""

    normalized = unicodedata.normalize("NFKD", value) #eg - Jöhn -> John
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", ascii_only.lower())
    return " ".join(cleaned.split())


def normalize_phone(value: str | None) -> str | None:
    """Normalize phone numbers into digits-only search keys."""

    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def normalize_email(value: str | None) -> str | None:
    """Normalize emails into lower-case search keys."""

    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


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

    @property
    def normalized_client_phone(self) -> str | None:
        return normalize_phone(self.client_phone)

    @property
    def normalized_client_email(self) -> str | None:
        return normalize_email(self.client_email)


@dataclass(slots=True)
class CaseSearchQuery:
    """Placeholder query model for later lookup layer work."""

    firm_id: str
    name: str
    limit: int = 10


@dataclass(slots=True)
class FirmRecord:
    """Tenant-level firm record."""

    firm_id: str
    name: str
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class FirmIntegrationRecord:
    """Provider-specific integration configuration for one firm."""

    integration_id: int | None = None
    firm_id: str = ""
    provider: str = ""
    provider_credentials: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    auto_sync_enabled: bool = False
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
