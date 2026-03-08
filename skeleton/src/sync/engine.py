"""Sync Engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.models.canonical import FirmIntegrationRecord, FirmRecord, StoredSyncState, SyncResult
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
    credentials: dict[str, Any] | None = None
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

        existing_firm = await self.repository.get_firm(request.firm_id)
        if existing_firm is None:
            await self.repository.save_firm(
                FirmRecord(
                    firm_id=request.firm_id,
                    name=request.firm_name or request.firm_id,
                )
            )
        elif request.firm_name and existing_firm.name != request.firm_name:
            await self.repository.save_firm(
                FirmRecord(
                    firm_id=existing_firm.firm_id,
                    name=request.firm_name,
                    is_active=existing_firm.is_active,
                )
            )

        credentials = await self._resolve_credentials(request)
        if not credentials:
            return SyncResult(
                firm_id=request.firm_id,
                provider=request.provider,
                records_fetched=0,
                records_saved=0,
                failed_records=0,
                success=False,
                error=f"Missing active integration credentials for {request.firm_id}/{request.provider}",
                started_at=started_at,
                completed_at=_utc_now(),
            )
        try:
            credentials = await self._refresh_credentials_if_needed(
                provider=provider,
                request=request,
                credentials=credentials,
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

        stored_state = await self.repository.get_sync_state(request.firm_id, request.provider)
        provider_sync_state = self._to_provider_sync_state(stored_state)

        try:
            provider_result = await self._sync_once(
                provider=provider,
                request=request,
                credentials=credentials,
                sync_state=provider_sync_state,
            )
        except ProviderConfigurationError:
            try:
                retry_credentials = await self._refresh_credentials_after_rejection(
                    provider=provider,
                    request=request,
                    credentials=credentials,
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
            if retry_credentials is None:
                return SyncResult(
                    firm_id=request.firm_id,
                    provider=request.provider,
                    records_fetched=0,
                    records_saved=0,
                    failed_records=0,
                    success=False,
                    error=f"Provider rejected credentials for {request.firm_id}/{request.provider}",
                    started_at=started_at,
                    completed_at=_utc_now(),
                )

            try:
                provider_result = await self._sync_once(
                    provider=provider,
                    request=request,
                    credentials=retry_credentials,
                    sync_state=provider_sync_state,
                )
                credentials = retry_credentials
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

    async def _resolve_credentials(self, request: SyncRequest) -> dict[str, Any]:
        if request.credentials:
            credentials = dict(request.credentials)
            await self.repository.save_firm_integration(
                FirmIntegrationRecord(
                    firm_id=request.firm_id,
                    provider=request.provider,
                    provider_credentials=credentials,
                )
            )
            return credentials

        integration = await self.repository.get_firm_integration(request.firm_id, request.provider)
        if integration is None or not integration.is_active:
            return {}
        return dict(integration.provider_credentials)

    async def _sync_once(
        self,
        *,
        provider: CaseManagementProvider,
        request: SyncRequest,
        credentials: dict[str, Any],
        sync_state: ProviderSyncState | None,
    ):
        return await provider.sync_cases(
            firm_id=request.firm_id,
            credentials=credentials,
            sync_state=sync_state,
        )

    async def _refresh_credentials_if_needed(
        self,
        *,
        provider: CaseManagementProvider,
        request: SyncRequest,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        needs_refresh = getattr(provider, "credentials_need_refresh", None)
        refresh = getattr(provider, "refresh_access_token", None)
        if not callable(needs_refresh) or not callable(refresh):
            return credentials
        if not needs_refresh(credentials):
            return credentials

        refreshed_credentials = await refresh(credentials)
        await self.repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id=request.firm_id,
                provider=request.provider,
                provider_credentials=refreshed_credentials,
            )
        )
        return refreshed_credentials

    async def _refresh_credentials_after_rejection(
        self,
        *,
        provider: CaseManagementProvider,
        request: SyncRequest,
        credentials: dict[str, Any],
    ) -> dict[str, Any] | None:
        refresh = getattr(provider, "refresh_access_token", None)
        if not callable(refresh):
            return None
        if not credentials.get("refresh_token"):
            return None

        refreshed_credentials = await refresh(credentials)
        await self.repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id=request.firm_id,
                provider=request.provider,
                provider_credentials=refreshed_credentials,
            )
        )
        return refreshed_credentials

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
