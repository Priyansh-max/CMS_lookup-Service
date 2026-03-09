"""Storage implementation."""

from __future__ import annotations

from datetime import timezone

from sqlalchemy import delete, desc, or_, select, text

from src.models.canonical import (
    CaseRecord,
    CaseSearchQuery,
    FieldMappingRecord,
    FirmIntegrationRecord,
    FirmRecord,
    StoredSyncState,
)
from src.storage.base import CaseRepository
from src.storage.database import (
    Base,
    CaseTable,
    FieldMappingTable,
    FirmIntegrationTable,
    FirmTable,
    SyncStateTable,
    create_engine_and_sessionmaker,
)


def _ensure_utc(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _to_case_record(row: CaseTable) -> CaseRecord:
    return CaseRecord(
        firm_id=row.firm_id,
        provider=row.provider,
        external_case_id=row.external_case_id,
        client_name=row.client_name,
        client_phone=row.client_phone,
        client_email=row.client_email,
        case_status=row.case_status,
        assigned_staff=row.assigned_staff,
        updated_at=_ensure_utc(row.updated_at),
        raw_payload=row.raw_payload or {},
    )


def _to_firm_record(row: FirmTable) -> FirmRecord:
    return FirmRecord(
        firm_id=row.id,
        name=row.name,
        is_active=row.is_active,
        created_at=_ensure_utc(row.created_at),
        updated_at=_ensure_utc(row.updated_at),
    )


def _to_firm_integration_record(row: FirmIntegrationTable) -> FirmIntegrationRecord:
    return FirmIntegrationRecord(
        integration_id=row.id,
        firm_id=row.firm_id,
        provider=row.provider,
        provider_credentials=row.provider_credentials or {},
        is_active=row.is_active,
        auto_sync_enabled=row.auto_sync_enabled,
        created_at=_ensure_utc(row.created_at),
        updated_at=_ensure_utc(row.updated_at),
    )


class RepositoryError(Exception):
    """Raised when repository operations fail."""


class CaseRepositoryImpl(CaseRepository):
    """SQLAlchemy-backed repository implementation."""

    def __init__(self, database_url: str):
        self.engine, self.session_factory = create_engine_and_sessionmaker(database_url)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text(
                    "ALTER TABLE firm_integrations "
                    "ADD COLUMN IF NOT EXISTS auto_sync_enabled BOOLEAN DEFAULT FALSE NOT NULL"
                )
            )

    async def save_firm(self, firm: FirmRecord) -> None:
        async with self.session_factory() as session:
            result = await session.execute(select(FirmTable).where(FirmTable.id == firm.firm_id))
            existing = result.scalar_one_or_none()

            if existing is None:
                session.add(
                    FirmTable(
                        id=firm.firm_id,
                        name=firm.name,
                        is_active=firm.is_active,
                    )
                )
            else:
                existing.name = firm.name
                existing.is_active = firm.is_active

            await session.commit()

    async def get_firm(self, firm_id: str) -> FirmRecord | None:
        async with self.session_factory() as session:
            result = await session.execute(select(FirmTable).where(FirmTable.id == firm_id))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _to_firm_record(row)

    async def list_firms(self) -> list[FirmRecord]:
        async with self.session_factory() as session:
            result = await session.execute(select(FirmTable).order_by(FirmTable.id))
            rows = result.scalars().all()
            return [_to_firm_record(row) for row in rows]

    async def save_firm_integration(self, integration: FirmIntegrationRecord) -> None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(FirmIntegrationTable).where(
                    FirmIntegrationTable.firm_id == integration.firm_id,
                    FirmIntegrationTable.provider == integration.provider,
                )
            )
            existing = result.scalar_one_or_none()

            if existing is None:
                session.add(
                    FirmIntegrationTable(
                        firm_id=integration.firm_id,
                        provider=integration.provider,
                        provider_credentials=integration.provider_credentials,
                        is_active=integration.is_active,
                        auto_sync_enabled=integration.auto_sync_enabled,
                    )
                )
            else:
                existing.provider_credentials = integration.provider_credentials
                existing.is_active = integration.is_active
                existing.auto_sync_enabled = integration.auto_sync_enabled

            await session.commit()

    async def get_firm_integration(
        self,
        firm_id: str,
        provider: str,
    ) -> FirmIntegrationRecord | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(FirmIntegrationTable).where(
                    FirmIntegrationTable.firm_id == firm_id,
                    FirmIntegrationTable.provider == provider,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _to_firm_integration_record(row)

    async def list_firm_integrations(self, firm_id: str) -> list[FirmIntegrationRecord]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(FirmIntegrationTable)
                .where(FirmIntegrationTable.firm_id == firm_id)
                .order_by(FirmIntegrationTable.provider)
            )
            rows = result.scalars().all()
            return [_to_firm_integration_record(row) for row in rows]

    async def save_case(self, case: CaseRecord) -> None:
        await self.save_cases([case])

    async def save_cases(self, cases: list[CaseRecord]) -> None:
        if not cases:
            return

        async with self.session_factory() as session:
            for case in cases:
                result = await session.execute(
                    select(CaseTable).where(
                        CaseTable.firm_id == case.firm_id,
                        CaseTable.provider == case.provider,
                        CaseTable.external_case_id == case.external_case_id,
                    )
                )
                existing = result.scalar_one_or_none()

                if existing is None:
                    session.add(
                        CaseTable(
                            firm_id=case.firm_id,
                            provider=case.provider,
                            external_case_id=case.external_case_id,
                            client_name=case.client_name,
                            normalized_client_name=case.normalized_client_name,
                            normalized_client_phone=case.normalized_client_phone,
                            normalized_client_email=case.normalized_client_email,
                            client_phone=case.client_phone,
                            client_email=case.client_email,
                            case_status=case.case_status,
                            assigned_staff=case.assigned_staff,
                            updated_at=case.updated_at,
                            raw_payload=case.raw_payload,
                        )
                    )
                    continue

                existing.client_name = case.client_name
                existing.normalized_client_name = case.normalized_client_name
                existing.normalized_client_phone = case.normalized_client_phone
                existing.normalized_client_email = case.normalized_client_email
                existing.client_phone = case.client_phone
                existing.client_email = case.client_email
                existing.case_status = case.case_status
                existing.assigned_staff = case.assigned_staff
                existing.updated_at = case.updated_at
                existing.raw_payload = case.raw_payload

            await session.commit()

    async def find_candidates_by_name(self, query: CaseSearchQuery) -> list[CaseRecord]:
        normalized_name = query.name.strip().lower()
        tokens = [token for token in normalized_name.split() if token]

        async with self.session_factory() as session:
            rows = []

            if normalized_name:
                exact_stmt = (
                    select(CaseTable)
                    .where(
                        CaseTable.firm_id == query.firm_id,
                        CaseTable.normalized_client_name == normalized_name,
                    )
                    .order_by(desc(CaseTable.updated_at))
                    .limit(query.limit)
                )
                exact_result = await session.execute(exact_stmt)
                exact_rows = exact_result.scalars().all()
                if exact_rows:
                    return [_to_case_record(row) for row in exact_rows]

                prefix_filters = [CaseTable.normalized_client_name.startswith(normalized_name)]
                prefix_filters.extend(
                    CaseTable.normalized_client_name.startswith(token) for token in tokens[:3]
                )
                prefix_stmt = (
                    select(CaseTable)
                    .where(
                        CaseTable.firm_id == query.firm_id,
                        or_(*prefix_filters),
                    )
                    .order_by(desc(CaseTable.updated_at))
                    .limit(max(query.limit * 4, 20))
                )
                prefix_result = await session.execute(prefix_stmt)
                rows = prefix_result.scalars().all()

                if not rows:
                    contains_filters = [CaseTable.normalized_client_name.contains(normalized_name)]
                    contains_filters.extend(
                        CaseTable.normalized_client_name.contains(token) for token in tokens[:3]
                    )
                    contains_stmt = (
                        select(CaseTable)
                        .where(
                            CaseTable.firm_id == query.firm_id,
                            or_(*contains_filters),
                        )
                        .order_by(desc(CaseTable.updated_at))
                        .limit(max(query.limit * 10, 25))
                    )
                    contains_result = await session.execute(contains_stmt)
                    rows = contains_result.scalars().all()

            # If the search terms are too noisy to match anything, return a small
            # tenant-scoped fallback set so the lookup layer can still rank results.
            if not rows:
                fallback = await session.execute(
                    select(CaseTable)
                    .where(CaseTable.firm_id == query.firm_id)
                    .order_by(desc(CaseTable.updated_at))
                    .limit(50)
                )
                rows = fallback.scalars().all()

            return [_to_case_record(row) for row in rows]

    async def get_case_by_external_id(
        self,
        firm_id: str,
        provider: str,
        external_case_id: str,
    ) -> CaseRecord | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(CaseTable).where(
                    CaseTable.firm_id == firm_id,
                    CaseTable.provider == provider,
                    CaseTable.external_case_id == external_case_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _to_case_record(row)

    async def get_sync_state(self, firm_id: str, provider: str) -> StoredSyncState | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(SyncStateTable).where(
                    SyncStateTable.firm_id == firm_id,
                    SyncStateTable.provider == provider,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return StoredSyncState(
                firm_id=row.firm_id,
                provider=row.provider,
                since=_ensure_utc(row.since),
                cursor=row.cursor,
                page_token=row.page_token,
                metadata=row.state_metadata or {},
            )

    async def upsert_sync_state(self, sync_state: StoredSyncState) -> None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(SyncStateTable).where(
                    SyncStateTable.firm_id == sync_state.firm_id,
                    SyncStateTable.provider == sync_state.provider,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(
                    SyncStateTable(
                        firm_id=sync_state.firm_id,
                        provider=sync_state.provider,
                        since=sync_state.since,
                        cursor=sync_state.cursor,
                        page_token=sync_state.page_token,
                        state_metadata=sync_state.metadata,
                    )
                )
            else:
                existing.since = sync_state.since
                existing.cursor = sync_state.cursor
                existing.page_token = sync_state.page_token
                existing.state_metadata = sync_state.metadata
            await session.commit()

    async def get_field_mappings(
        self,
        firm_id: str,
        provider: str,
    ) -> dict[str, list[str]]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(FieldMappingTable).where(
                    FieldMappingTable.firm_id == firm_id,
                    FieldMappingTable.provider == provider,
                )
            )
            rows = result.scalars().all()
            return {row.canonical_field: list(row.source_fields or []) for row in rows}

    async def save_field_mappings(self, mappings: list[FieldMappingRecord]) -> None:
        if not mappings:
            return

        firm_id = mappings[0].firm_id
        provider = mappings[0].provider

        async with self.session_factory() as session:
            await session.execute(
                delete(FieldMappingTable).where(
                    FieldMappingTable.firm_id == firm_id,
                    FieldMappingTable.provider == provider,
                )
            )
            session.add_all(
                [
                    FieldMappingTable(
                        firm_id=mapping.firm_id,
                        provider=mapping.provider,
                        canonical_field=mapping.canonical_field,
                        source_fields=mapping.source_fields,
                    )
                    for mapping in mappings
                ]
            )
            await session.commit()

    async def close(self) -> None:
        await self.engine.dispose()
