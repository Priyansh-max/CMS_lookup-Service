"""Filevine data transformer."""

from __future__ import annotations

from typing import Any

from src.models.canonical import CaseRecord
from src.transformers.base import CaseTransformer, TransformerPayloadError


class FilevineTransformer(CaseTransformer):
    """Transform Filevine payloads into canonical case records."""

    default_mappings = {
        "external_case_id": ["project.project_id", "project.id", "id"],
        # Filevine names may be split; the shared helper joins multiple paths.
        "client_name": ["contact.full_name", "contact.first_name", "contact.last_name"],
        "client_phone": ["contact.mobile_phone", "contact.phone", "contact.primary_phone"],
        "client_email": ["contact.email", "contact.primary_email"],
        "case_status": ["project.phase", "project.status"],
        "assigned_staff": ["project.primary_attorney", "project.assigned_staff"],
        "updated_at": ["project.last_activity_at", "updated_at"],
    }

    def transform(
        self,
        raw_data: dict[str, Any],
        *,
        firm_id: str,
        mapping_overrides: dict[str, list[str]] | None = None,
    ) -> CaseRecord:
        if not isinstance(raw_data, dict):
            raise TransformerPayloadError("Filevine transformer expects an object payload")

        external_case_id = self.require_field(
            self.resolve_field(raw_data, "external_case_id", mapping_overrides),
            "external_case_id",
        )

        client_name = self.require_field(
            self.resolve_field(raw_data, "client_name", mapping_overrides),
            "client_name",
        )

        updated_at_raw = self.resolve_field(raw_data, "updated_at", mapping_overrides)

        return CaseRecord(
            firm_id=firm_id,
            provider="filevine",
            external_case_id=external_case_id,
            client_name=client_name,
            client_phone=self.resolve_field(raw_data, "client_phone", mapping_overrides),
            client_email=self.resolve_field(raw_data, "client_email", mapping_overrides),
            case_status=self.resolve_field(raw_data, "case_status", mapping_overrides),
            assigned_staff=self.resolve_field(raw_data, "assigned_staff", mapping_overrides),
            updated_at=self.parse_datetime(updated_at_raw, "updated_at"),
            raw_payload=raw_data,
        )
