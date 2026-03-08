"""Abstract base class for data transformers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from src.models.canonical import CaseRecord


class TransformerError(Exception):
    """Base exception for transformation failures."""


class TransformerPayloadError(TransformerError):
    """Raised when the raw payload shape is unusable."""


class TransformerValidationError(TransformerError):
    """Raised when required canonical fields are missing."""


class CaseTransformer(ABC):
    """Base transformer with shared helper methods."""

    default_mappings: dict[str, list[str]] = {}

    @abstractmethod
    def transform(
        self,
        raw_data: dict[str, Any],
        *,
        firm_id: str,
        mapping_overrides: dict[str, list[str]] | None = None,
    ) -> CaseRecord:
        """Convert one raw provider payload into a canonical case record."""

    def resolve_field(
        self,
        raw_data: dict[str, Any],
        canonical_field: str,
        mapping_overrides: dict[str, list[str]] | None = None,
        *,
        separator: str = " ",
    ) -> str | None:
        paths = None
        if mapping_overrides:
            paths = mapping_overrides.get(canonical_field)
        if not paths:
            paths = self.default_mappings.get(canonical_field, [])

        values = [self.get_nested_value(raw_data, path) for path in paths]
        values = [str(value).strip() for value in values if value not in (None, "")]
        if not values:
            return None
        return separator.join(values)

    def require_field(self, value: str | None, field_name: str) -> str:
        if not value:
            raise TransformerValidationError(
                f"Missing required canonical field: {field_name}"
            )
        return value

    def parse_datetime(self, value: Any, field_name: str) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise TransformerValidationError(
                f"Invalid datetime for field {field_name}: {value!r}"
            )

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TransformerValidationError(
                f"Invalid datetime for field {field_name}: {value!r}"
            ) from exc

    def get_nested_value(self, payload: dict[str, Any], path: str) -> Any:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
        return current
