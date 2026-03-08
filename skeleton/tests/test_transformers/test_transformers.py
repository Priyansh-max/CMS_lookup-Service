from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.transformers.base import TransformerPayloadError, TransformerValidationError
from src.transformers.clio_transformer import ClioTransformer
from src.transformers.filevine_transformer import FilevineTransformer


def test_clio_transformer_maps_canonical_fields() -> None:
    transformer = ClioTransformer()
    raw_payload = {
        "id": "matter-1",
        "client": {
            "name": "John Smith",
            "phone_number": "111-222-3333",
            "email": "john@example.com",
        },
        "status": "open",
        "responsible_attorney": {"name": "A. Lawyer"},
        "updated_at": "2024-01-01T00:00:00Z",
    }

    case = transformer.transform(raw_payload, firm_id="firm-1")

    assert case.provider == "clio"
    assert case.external_case_id == "matter-1"
    assert case.client_name == "John Smith"
    assert case.client_phone == "111-222-3333"
    assert case.client_email == "john@example.com"
    assert case.case_status == "open"
    assert case.assigned_staff == "A. Lawyer"
    assert case.updated_at == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_clio_transformer_supports_mapping_overrides() -> None:
    transformer = ClioTransformer()
    raw_payload = {
        "alt_id": "matter-99",
        "person": {"full_name": "Jane Doe"},
    }

    case = transformer.transform(
        raw_payload,
        firm_id="firm-1",
        mapping_overrides={
            "external_case_id": ["alt_id"],
            "client_name": ["person.full_name"],
        },
    )

    assert case.external_case_id == "matter-99"
    assert case.client_name == "Jane Doe"


def test_clio_transformer_rejects_invalid_payload_shape() -> None:
    transformer = ClioTransformer()

    with pytest.raises(TransformerPayloadError):
        transformer.transform(["not", "a", "dict"], firm_id="firm-1")  # type: ignore[arg-type]


def test_clio_transformer_requires_identity_fields() -> None:
    transformer = ClioTransformer()

    with pytest.raises(TransformerValidationError):
        transformer.transform({"client": {"name": "John Smith"}}, firm_id="firm-1")


def test_filevine_transformer_joins_split_name_fields() -> None:
    transformer = FilevineTransformer()
    raw_payload = {
        "project": {
            "project_id": "project-1",
            "phase": "intake",
            "primary_attorney": "Case Owner",
            "last_activity_at": "2024-02-02T00:00:00Z",
        },
        "contact": {
            "first_name": "Jon",
            "last_name": "Smyth",
            "mobile_phone": "222-333-4444",
            "email": "jon@example.com",
        },
    }

    case = transformer.transform(raw_payload, firm_id="firm-2")

    assert case.provider == "filevine"
    assert case.external_case_id == "project-1"
    assert case.client_name == "Jon Smyth"
    assert case.client_phone == "222-333-4444"
    assert case.client_email == "jon@example.com"
    assert case.case_status == "intake"
    assert case.assigned_staff == "Case Owner"
    assert case.updated_at == datetime(2024, 2, 2, tzinfo=timezone.utc)


def test_filevine_transformer_supports_mapping_overrides() -> None:
    transformer = FilevineTransformer()
    raw_payload = {
        "alt_project_id": "project-9",
        "person": {"name": "Client Override"},
    }

    case = transformer.transform(
        raw_payload,
        firm_id="firm-2",
        mapping_overrides={
            "external_case_id": ["alt_project_id"],
            "client_name": ["person.name"],
        },
    )

    assert case.external_case_id == "project-9"
    assert case.client_name == "Client Override"


def test_filevine_transformer_rejects_invalid_datetime() -> None:
    transformer = FilevineTransformer()
    raw_payload = {
        "project": {"project_id": "project-2", "last_activity_at": "not-a-date"},
        "contact": {"full_name": "John Smith"},
    }

    with pytest.raises(TransformerValidationError):
        transformer.transform(raw_payload, firm_id="firm-2")
