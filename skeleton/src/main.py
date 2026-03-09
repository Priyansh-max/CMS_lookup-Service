"""Entry point for the CMS Integration Layer."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Any
from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.api.case_lookup import CaseLookupService
from src.models.canonical import FieldMappingRecord, FirmIntegrationRecord, FirmRecord
from src.providers import (
    ClioProvider,
    FilevineProvider,
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderTemporaryError,
)
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
    firm_name: str | None = None


class FirmPayload(BaseModel):
    firm_id: str
    name: str
    is_active: bool = True


class FirmIntegrationPayload(BaseModel):
    provider: str
    provider_credentials: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    auto_sync_enabled: bool = False


class ClioBootstrapPayload(BaseModel):
    firm_id: str
    code: str | None = None
    is_active: bool = True
    auto_sync_enabled: bool = False


class FilevineBootstrapPayload(BaseModel):
    firm_id: str
    pat: str
    is_active: bool = True
    auto_sync_enabled: bool = False


class SyncBatchPayload(BaseModel):
    requests: list[SyncRequestPayload]


class MappingPayload(BaseModel):
    provider: str
    mappings: dict[str, list[str]]


async def _bootstrap_clio_integration(
    *,
    repository: CaseRepositoryImpl,
    provider_client: ClioProvider,
    firm_id: str,
    code: str,
    is_active: bool = True,
    auto_sync_enabled: bool = False,
) -> dict[str, Any]:
    firm = await repository.get_firm(firm_id)
    if firm is None:
        raise HTTPException(status_code=404, detail=f"Unknown firm_id: {firm_id}")

    try:
        token_response = await provider_client.exchange_code_for_token(code)
        integration_credentials = provider_client.build_integration_credentials(token_response)
    except (ProviderConfigurationError, ProviderPayloadError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderTemporaryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await repository.save_firm_integration(
        FirmIntegrationRecord(
            firm_id=firm_id,
            provider="clio",
            provider_credentials=integration_credentials,
            is_active=is_active,
            auto_sync_enabled=auto_sync_enabled,
        )
    )
    return {
        "firm_id": firm_id,
        "provider": "clio",
        "bootstrapped": True,
        "has_refresh_token": bool(integration_credentials.get("refresh_token")),
        "token_expires_at": integration_credentials.get("token_expires_at"),
        "is_active": is_active,
        "auto_sync_enabled": auto_sync_enabled,
    }


def build_default_sync_requests() -> list[SyncRequest]:
    requests: list[SyncRequest] = []

    filevine_sample_path = os.getenv("FILEVINE_SAMPLE_PATH")
    filevine_firm_id = os.getenv("FILEVINE_FIRM_ID", "firm-filevine")
    if filevine_sample_path:
        requests.append(
            SyncRequest(
                firm_id=filevine_firm_id,
                provider="filevine",
                credentials={"sample_path": filevine_sample_path},
                firm_name=filevine_firm_id,
            )
        )

    return requests


def create_app() -> FastAPI:
    database_url = os.getenv("DATABASE_URL")

    if(database_url is None):
        raise ValueError("DATABASE_URL is not set")

    repository = CaseRepositoryImpl(database_url)
    providers = {
        "clio": ClioProvider(
            api_base_url=os.getenv("CLIO_API_BASE_URL", "https://app.clio.com/api/v4"),
            oauth_authorize_url=os.getenv(
                "CLIO_AUTH_URL",
                "https://app.clio.com/oauth/authorize",
            ),
            oauth_token_url=os.getenv("CLIO_TOKEN_URL", "https://app.clio.com/oauth/token"),
            client_id=os.getenv("CLIO_CLIENT_ID"),
            client_secret=os.getenv("CLIO_CLIENT_SECRET"),
            redirect_uri=os.getenv("CLIO_REDIRECT_URI"),
            scopes=[
                scope.strip()
                for scope in os.getenv("CLIO_SCOPES", "").split(",")
                if scope.strip()
            ],
        ),
        "filevine": FilevineProvider(
            default_sample_path=os.getenv("FILEVINE_SAMPLE_PATH"),
            identity_url=os.getenv(
                "FILEVINE_IDENTITY_URL",
                "https://identity.filevine.com/connect/token",
            ),
            org_lookup_url=os.getenv(
                "FILEVINE_ORG_LOOKUP_URL",
                "https://api.filevineapp.com/fv-app/v2/utils/GetUserOrgsWithToken",
            ),
            projects_url=os.getenv(
                "FILEVINE_PROJECTS_URL",
                "https://api.filevineapp.com/fv-app/v2/Projects",
            ),
            client_id=os.getenv("FILEVINE_CLIENT_ID"),
            client_secret=os.getenv("FILEVINE_CLIENT_SECRET"),
            scopes=os.getenv(
                "FILEVINE_SCOPES",
                "fv.api.gateway.access tenant filevine.v2.api.* openid email fv.auth.tenant.read",
            ),
            user_agent=os.getenv(
                "FILEVINE_USER_AGENT",
                "firm-cms-integration-service/1.0",
            ),
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
        repository=repository,
        requests=build_default_sync_requests(),
        interval_seconds=int(os.getenv("SYNC_INTERVAL_SECONDS", "300")),
    )
    scheduler_enabled = _env_bool(os.getenv("SCHEDULER_ENABLED"), default=False)

    async def _refresh_scheduler_requests() -> None:
        await scheduler.refresh_schedule()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await repository.initialize()
        if scheduler_enabled:
            await scheduler.start()
        else:
            await scheduler.refresh_requests()
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

    @app.post("/auth/clio/bootstrap")
    async def clio_bootstrap(payload: ClioBootstrapPayload) -> dict[str, Any]:
        firm = await repository.get_firm(payload.firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail=f"Unknown firm_id: {payload.firm_id}")

        if payload.code is None:
            try:
                authorization_url = providers["clio"].build_authorize_url(state=payload.firm_id)
            except ProviderConfigurationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            return {
                "firm_id": payload.firm_id,
                "provider": "clio",
                "authorization_url": authorization_url,
                "bootstrapped": False,
            }

        result = await _bootstrap_clio_integration(
            repository=repository,
            provider_client=providers["clio"],
            firm_id=payload.firm_id,
            code=payload.code,
            is_active=payload.is_active,
            auto_sync_enabled=payload.auto_sync_enabled,
        )
        await _refresh_scheduler_requests()
        return result

    @app.post("/auth/filevine/bootstrap")
    async def filevine_bootstrap(payload: FilevineBootstrapPayload) -> dict[str, Any]:
        firm = await repository.get_firm(payload.firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail=f"Unknown firm_id: {payload.firm_id}")

        if not providers["filevine"].client_id or not providers["filevine"].client_secret:
            raise HTTPException(
                status_code=400,
                detail="Missing Filevine client credentials in app configuration",
            )

        await repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id=payload.firm_id,
                provider="filevine",
                provider_credentials={"pat": payload.pat},
                is_active=payload.is_active,
                auto_sync_enabled=payload.auto_sync_enabled,
            )
        )
        await _refresh_scheduler_requests()
        return {
            "firm_id": payload.firm_id,
            "provider": "filevine",
            "bootstrapped": True,
            "stored_credentials": ["pat"],
            "is_active": payload.is_active,
            "auto_sync_enabled": payload.auto_sync_enabled,
        }

    @app.get("/firms")
    async def list_firms() -> list[dict[str, Any]]:
        firms = await repository.list_firms()
        return [
            {
                "firm_id": firm.firm_id,
                "name": firm.name,
                "is_active": firm.is_active,
            }
            for firm in firms
        ]

    @app.post("/firms")
    async def save_firm(payload: FirmPayload) -> dict[str, Any]:
        await repository.save_firm(
            FirmRecord(
                firm_id=payload.firm_id,
                name=payload.name,
                is_active=payload.is_active,
            )
        )
        await _refresh_scheduler_requests()
        return {
            "firm_id": payload.firm_id,
            "name": payload.name,
            "is_active": payload.is_active,
        }

    @app.get("/firms/{firm_id}/integrations")
    async def list_integrations(firm_id: str) -> list[dict[str, Any]]:
        firm = await repository.get_firm(firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail=f"Unknown firm_id: {firm_id}")

        integrations = await repository.list_firm_integrations(firm_id)
        return [
            {
                "integration_id": integration.integration_id,
                "firm_id": integration.firm_id,
                "provider": integration.provider,
                "has_credentials": bool(integration.provider_credentials),
                "is_active": integration.is_active,
                "auto_sync_enabled": integration.auto_sync_enabled,
            }
            for integration in integrations
        ]

    @app.post("/firms/{firm_id}/integrations")
    async def save_integration(
        firm_id: str,
        payload: FirmIntegrationPayload,
    ) -> dict[str, Any]:
        firm = await repository.get_firm(firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail=f"Unknown firm_id: {firm_id}")

        await repository.save_firm_integration(
            FirmIntegrationRecord(
                firm_id=firm_id,
                provider=payload.provider,
                provider_credentials=payload.provider_credentials,
                is_active=payload.is_active,
                auto_sync_enabled=payload.auto_sync_enabled,
            )
        )
        await _refresh_scheduler_requests()
        return {
            "firm_id": firm_id,
            "provider": payload.provider,
            "is_active": payload.is_active,
            "auto_sync_enabled": payload.auto_sync_enabled,
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
                    credentials=item.credentials or None,
                    firm_name=item.firm_name,
                )
                for item in payload.requests
            ]
        else:
            requests = await scheduler.refresh_requests()

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

    #the field mapping endpoint used for firm-specific field mapping used to create and stores the mapping in field mapping table which is later used to create mapping_overrides in sync engine which is prefered over the default mapping in the sync engine (default mapping == provider specific mapping)
    @app.post("/firms/{firm_id}/mapping")
    async def save_mappings(firm_id: str, payload: MappingPayload) -> dict[str, Any]:
        firm = await repository.get_firm(firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail=f"Unknown firm_id: {firm_id}")

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

if os.getenv("DATABASE_URL"):
    app = create_app()
else:
    app = FastAPI(title="CMS Integration Layer")


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
