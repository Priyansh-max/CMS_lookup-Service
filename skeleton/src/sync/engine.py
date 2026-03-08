"""Sync Engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.models.canonical import FirmRecord, StoredSyncState, SyncResult
from src.providers.base import (
    CaseManagementProvider,
    ProviderError,
    ProviderSyncState,
)
from src.storage.base import CaseRepository
from src.transformers.base import CaseTransformer, TransformerError


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(slots=True)
class SyncRequest:
    """Explicit sync input until firm/provider config is stored in the database."""

    firm_id: str
    provider: str
    credentials: dict[str, object]
    firm_name: str | None = None


class SyncEngine:
    """Coordinates provider fetch, transformation, persistence, and sync-state updates."""

    def __init__(
        self,
        *,
        repository: CaseRepository,
        providers: dict[str, CaseManagementProvider],
        transformers: dict[str, CaseTransformer],
    ):
        self.repository = repository
        self.providers = providers
        self.transformers = transformers

    async def sync_provider(self, request: SyncRequest) -> SyncResult:
        started_at = _utc_now()

        provider = self.providers.get(request.provider)
        transformer = self.transformers.get(request.provider)
        if provider is None or transformer is None:
            return SyncResult(
                firm_id=request.firm_id,
                provider=request.provider,
                records_fetched=0,
                records_saved=0,
                failed_records=0,
                success=False,
                error=f"Missing provider or transformer for {request.provider}",
                started_at=started_at,
                completed_at=_utc_now(),
            )

        await self.repository.save_firm(
            FirmRecord(
                firm_id=request.firm_id,
                name=request.firm_name or request.firm_id,
                provider=request.provider,
                provider_credentials=dict(request.credentials),
            )
        )

        stored_state = await self.repository.get_sync_state(request.firm_id, request.provider)
        provider_sync_state = self._to_provider_sync_state(stored_state)

        try:
            provider_result = await provider.sync_cases(
                firm_id=request.firm_id,
                credentials=request.credentials,
                sync_state=provider_sync_state,
            )
        except ProviderError as exc:
            return SyncResult(
                firm_id=request.firm_id,
                provider=request.provider,
                records_fetched=0,
                records_saved=0,
                failed_records=0,
                success=False,
                error=str(exc),
                started_at=started_at,
                completed_at=_utc_now(),
            )

        mapping_overrides = await self.repository.get_field_mappings(
            request.firm_id,
            request.provider,
        )

        transformed_records = []
        failed_records = 0

        for raw_record in provider_result.records:
            try:
                transformed_records.append(
                    transformer.transform(
                        raw_record,
                        firm_id=request.firm_id,
                        mapping_overrides=mapping_overrides,
                    )
                )
            except TransformerError:
                # We skip bad records but keep count so the sync result reflects
                # that the provider run was only partially successful.
                failed_records += 1

        try:
            await self.repository.save_cases(transformed_records)
        except Exception as exc:
            return SyncResult(
                firm_id=request.firm_id,
                provider=request.provider,
                records_fetched=len(provider_result.records),
                records_saved=0,
                failed_records=failed_records,
                success=False,
                error=f"Repository write failed: {exc}",
                is_snapshot=provider_result.is_snapshot,
                started_at=started_at,
                completed_at=_utc_now(),
            )

        if provider_result.next_state is not None:
            next_state = self._to_stored_sync_state(
                firm_id=request.firm_id,
                provider=request.provider,
                provider_state=provider_result.next_state,
            )
            await self.repository.upsert_sync_state(next_state)

        completed_at = _utc_now()
        partial_failure = failed_records > 0
        success = not partial_failure
        error = None
        if partial_failure:
            error = f"{failed_records} record(s) failed transformation"

        return SyncResult(
            firm_id=request.firm_id,
            provider=request.provider,
            records_fetched=len(provider_result.records),
            records_saved=len(transformed_records),
            failed_records=failed_records,
            success=success,
            partial_failure=partial_failure,
            error=error,
            is_snapshot=provider_result.is_snapshot,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def sync_many(self, requests: list[SyncRequest]) -> list[SyncResult]:
        results = []
        for request in requests:
            results.append(await self.sync_provider(request))
        return results

    def _to_provider_sync_state(
        self,
        stored_state: StoredSyncState | None,
    ) -> ProviderSyncState | None:
        if stored_state is None:
            return None
        return ProviderSyncState(
            since=stored_state.since,
            cursor=stored_state.cursor,
            page_token=stored_state.page_token,
            metadata=stored_state.metadata,
        )

    def _to_stored_sync_state(
        self,
        *,
        firm_id: str,
        provider: str,
        provider_state: ProviderSyncState,
    ) -> StoredSyncState:
        return StoredSyncState(
            firm_id=firm_id,
            provider=provider,
            since=provider_state.since,
            cursor=provider_state.cursor,
            page_token=provider_state.page_token,
            metadata=provider_state.metadata,
        )
