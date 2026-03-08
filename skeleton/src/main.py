"""Entry point for the CMS Integration Layer."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.api.case_lookup import CaseLookupService
from src.models.canonical import FieldMappingRecord
from src.providers import ClioProvider, FilevineProvider
from src.storage import CaseRepositoryImpl
from src.sync import SyncEngine, SyncRequest, SyncScheduler
from src.transformers import ClioTransformer, FilevineTransformer


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class LookupResponse(BaseModel):
    firm_id: str
    provider: str
    external_case_id: str
    client_name: str
    client_phone: str | None
    client_email: str | None
    case_status: str | None
    assigned_staff: str | None
    score: float
    match_type: str
#score and match type generated using fuzzy matching will be used to rank the match and also the agent can use it to understand the match quality

#sync request payload when syncing it requires the firm id, which provider to sync from and the credentials for the provider (to access the provider's data)
class SyncRequestPayload(BaseModel):
    firm_id: str
    provider: str
    credentials: dict[str, Any] = Field(default_factory=dict)


class SyncBatchPayload(BaseModel):
    requests: list[SyncRequestPayload]


class MappingPayload(BaseModel):
    provider: str
    mappings: dict[str, list[str]]


def build_default_sync_requests() -> list[SyncRequest]:
    requests: list[SyncRequest] = []

    clio_access_token = os.getenv("CLIO_ACCESS_TOKEN")
    clio_firm_id = os.getenv("CLIO_FIRM_ID", "firm-clio")
    if clio_access_token:
        requests.append(
            SyncRequest(
                firm_id=clio_firm_id,
                provider="clio",
                credentials={"access_token": clio_access_token},
            )
        )

    filevine_sample_path = os.getenv("FILEVINE_SAMPLE_PATH")
    filevine_firm_id = os.getenv("FILEVINE_FIRM_ID", "firm-filevine")
    if filevine_sample_path:
        requests.append(
            SyncRequest(
                firm_id=filevine_firm_id,
                provider="filevine",
                credentials={"sample_path": filevine_sample_path},
            )
        )

    return requests


def create_app() -> FastAPI:
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./cms_integration.db")
    repository = CaseRepositoryImpl(database_url)
    providers = {
        "clio": ClioProvider(
            api_base_url=os.getenv("CLIO_API_BASE_URL", "https://app.clio.com/api/v4")
        ),
        "filevine": FilevineProvider(
            default_sample_path=os.getenv("FILEVINE_SAMPLE_PATH")
        ),
    }
    transformers = {
        "clio": ClioTransformer(),
        "filevine": FilevineTransformer(),
    }
    sync_engine = SyncEngine(
        repository=repository,
        providers=providers,
        transformers=transformers,
    )
    lookup_service = CaseLookupService(repository)
    scheduler = SyncScheduler(
        sync_engine=sync_engine,
        requests=build_default_sync_requests(),
        interval_seconds=int(os.getenv("SYNC_INTERVAL_SECONDS", "300")),
    )
    scheduler_enabled = _env_bool(os.getenv("SCHEDULER_ENABLED"), default=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await repository.initialize()
        if scheduler_enabled and scheduler.requests:
            await scheduler.start()
        try:
            yield
        finally:
            if scheduler_enabled and scheduler._started:
                await scheduler.stop()
            await repository.close()

    app = FastAPI(title="CMS Integration Layer", lifespan=lifespan)
    app.state.repository = repository
    app.state.sync_engine = sync_engine
    app.state.lookup_service = lookup_service
    app.state.scheduler = scheduler
    app.state.scheduler_enabled = scheduler_enabled

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "scheduler_enabled": scheduler_enabled,
            "configured_sync_requests": len(scheduler.requests),
        }

    #the case lookup endpoint
    @app.get("/cases/lookup", response_model=list[LookupResponse])
    async def lookup_cases(firm_id: str, name: str) -> list[LookupResponse]:
        matches = await lookup_service.lookup_by_name(name=name, firm_id=firm_id)
        return [
            LookupResponse(
                firm_id=match.case.firm_id,
                provider=match.case.provider,
                external_case_id=match.case.external_case_id,
                client_name=match.case.client_name,
                client_phone=match.case.client_phone,
                client_email=match.case.client_email,
                case_status=match.case.case_status,
                assigned_staff=match.case.assigned_staff,
                score=match.score,
                match_type=match.match_type,
            )
            for match in matches
        ]

    #the manual sync endpoint
    @app.post("/sync")
    async def run_sync(payload: SyncBatchPayload | None = None) -> list[dict[str, Any]]:
        if payload is not None:
            requests = [
                SyncRequest(
                    firm_id=item.firm_id,
                    provider=item.provider,
                    credentials=item.credentials,
                )
                for item in payload.requests
            ]
        else:
            requests = scheduler.requests

        if not requests:
            raise HTTPException(
                status_code=400,
                detail="No sync requests configured or provided",
            )

        results = await sync_engine.sync_many(requests)
        return [
            {
                "firm_id": result.firm_id,
                "provider": result.provider,
                "records_fetched": result.records_fetched,
                "records_saved": result.records_saved,
                "failed_records": result.failed_records,
                "success": result.success,
                "partial_failure": result.partial_failure,
                "error": result.error,
                "is_snapshot": result.is_snapshot,
            }
            for result in results
        ]

    #the field mapping endpoint (task for advanced stage)
    @app.post("/firms/{firm_id}/mapping")
    async def save_mappings(firm_id: str, payload: MappingPayload) -> dict[str, Any]:
        records = [
            FieldMappingRecord(
                firm_id=firm_id,
                provider=payload.provider,
                canonical_field=canonical_field,
                source_fields=source_fields,
            )
            for canonical_field, source_fields in payload.mappings.items()
        ]
        await repository.save_field_mappings(records)
        return {
            "firm_id": firm_id,
            "provider": payload.provider,
            "saved_mappings": len(records),
        }

    return app


app = create_app()


def main() -> None:
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
