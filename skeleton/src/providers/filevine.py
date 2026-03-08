"""Filevine CMS Provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import httpx

from src.providers.base import (
    CaseManagementProvider,
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderSyncResult,
    ProviderSyncState,
    ProviderTemporaryError,
)


class FilevineProvider(CaseManagementProvider):
    """Load snapshot-style or live raw case payloads for Filevine."""

    def __init__(
        self,
        *,
        default_sample_path: str | None = None,
        identity_url: str = "https://identity.filevine.com/connect/token",
        org_lookup_url: str = "https://api.filevineapp.com/fv-app/v2/utils/GetUserOrgsWithToken",
        projects_url: str = "https://api.filevineapp.com/fv-app/v2/Projects",
        client_id: str | None = None,
        client_secret: str | None = None,
        scopes: str = "fv.api.gateway.access tenant filevine.v2.api.* openid email fv.auth.tenant.read",
        user_agent: str = "firm-cms-integration-service/1.0",
        timeout_seconds: float = 20.0,
        access_token_buffer_seconds: int = 60,
    ):
        self.default_sample_path = default_sample_path
        self.identity_url = identity_url
        self.org_lookup_url = org_lookup_url
        self.projects_url = projects_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.access_token_buffer_seconds = access_token_buffer_seconds

    @property
    def provider_name(self) -> str:
        return "filevine"

    def credentials_need_refresh(self, credentials: dict[str, Any]) -> bool:
        """Refresh the live Filevine bearer token from the PAT when needed."""

        if credentials.get("sample_path"):
            return False
        if not credentials.get("pat"):
            return False
        if not credentials.get("access_token"):
            return True
        if not credentials.get("user_id") or not credentials.get("org_id"):
            return True

        expires_at = self._parse_expires_at(credentials.get("token_expires_at"))
        if expires_at is None:
            return False

        refresh_at = expires_at - timedelta(seconds=self.access_token_buffer_seconds)
        return datetime.now(tz=timezone.utc) >= refresh_at

    async def refresh_access_token(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Exchange the Filevine PAT for a live bearer token and tenant context."""

        pat = credentials.get("pat")
        if not pat:
            raise ProviderConfigurationError("Missing Filevine PAT")
        if not self.client_id or not self.client_secret:
            raise ProviderConfigurationError(
                "Missing Filevine client credentials for live ingestion"
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                token_response = await client.post(
                    self.identity_url,
                    data={
                        "token": pat,
                        "grant_type": "personal_access_token",
                        "scope": self.scopes,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                token_response.raise_for_status()
                token_payload = token_response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while exchanging Filevine PAT") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {400, 401, 403}:
                raise ProviderConfigurationError(
                    "Filevine PAT or client credentials are invalid"
                ) from exc
            raise ProviderTemporaryError(
                f"Filevine token exchange failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Filevine token exchange request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Filevine token exchange returned invalid JSON") from exc

        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderPayloadError("Filevine token exchange missing access_token")

        user_id, org_id = await self._fetch_user_org_context(access_token)
        expires_at = None
        expires_in = token_payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))
            ).isoformat()

        refreshed_credentials = dict(credentials)
        refreshed_credentials["access_token"] = access_token
        refreshed_credentials["user_id"] = str(user_id)
        refreshed_credentials["org_id"] = str(org_id)
        refreshed_credentials["token_type"] = token_payload.get(
            "token_type",
            credentials.get("token_type", "Bearer"),
        )
        refreshed_credentials["scope"] = token_payload.get("scope", self.scopes)
        refreshed_credentials["refreshed_at"] = datetime.now(tz=timezone.utc).isoformat()
        if expires_at is not None:
            refreshed_credentials["token_expires_at"] = expires_at
        return refreshed_credentials

    async def sync_cases(
        self,
        *,
        firm_id: str,
        credentials: dict[str, Any],
        sync_state: ProviderSyncState | None = None,
    ) -> ProviderSyncResult:
        sample_path = credentials.get("sample_path") or self.default_sample_path
        if sample_path:
            return await self._sync_snapshot(sample_path=sample_path, sync_state=sync_state)

        access_token = credentials.get("access_token")
        user_id = credentials.get("user_id")
        org_id = credentials.get("org_id")
        if not access_token or not user_id or not org_id:
            raise ProviderConfigurationError(
                f"Missing Filevine live credentials for firm {firm_id}"
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self.projects_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "User-Agent": self.user_agent,
                        "x-fv-orgid": str(org_id),
                        "x-fv-userid": str(user_id),
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while calling Filevine") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                raise ProviderConfigurationError(
                    f"Filevine rejected credentials for firm {firm_id}"
                ) from exc
            raise ProviderTemporaryError(
                f"Filevine request failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Filevine request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Filevine returned invalid JSON") from exc

        records = self._extract_records(payload)
        latest_seen = self._latest_updated_at(records)

        next_state = ProviderSyncState(
            since=latest_seen or (sync_state.since if sync_state else None),
            metadata={
                "strategy": "live-pat",
                "record_count": len(records),
                "projects_url": self.projects_url,
                "org_id": str(org_id),
                "user_id": str(user_id),
            },
        )
        return ProviderSyncResult(records=records, next_state=next_state, is_snapshot=False)

    async def _sync_snapshot(
        self,
        *,
        sample_path: str,
        sync_state: ProviderSyncState | None,
    ) -> ProviderSyncResult:
        path = Path(sample_path)
        if not path.exists():
            raise ProviderConfigurationError(
                f"Filevine sample file does not exist: {path}"
            )

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderPayloadError("Filevine sample file contains invalid JSON") from exc

        records = self._extract_records(payload)
        next_state = ProviderSyncState(
            since=sync_state.since if sync_state else None,
            metadata={
                "strategy": "snapshot",
                "record_count": len(records),
                "source_path": str(path),
            },
        )
        return ProviderSyncResult(records=records, next_state=next_state, is_snapshot=True)

    async def _fetch_user_org_context(self, access_token: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.org_lookup_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "User-Agent": self.user_agent,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while loading Filevine org context") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {400, 401, 403}:
                raise ProviderConfigurationError(
                    "Filevine org lookup failed for the exchanged token"
                ) from exc
            raise ProviderTemporaryError(
                f"Filevine org lookup failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Filevine org lookup request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Filevine org lookup returned invalid JSON") from exc

        for candidate in self._iter_org_candidates(payload):
            user_id = candidate.get("user_id")
            org_id = candidate.get("org_id")
            if user_id is None:
                user_id = candidate.get("userId")
            if org_id is None:
                org_id = candidate.get("orgId")
            if user_id is not None and org_id is not None:
                return str(user_id), str(org_id)

        raise ProviderPayloadError("Filevine org lookup missing user_id/org_id")

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            for key in ("data", "records", "projects"):
                value = payload.get(key)
                if isinstance(value, list):
                    records = value
                    break
            else:
                raise ProviderPayloadError(
                    "Filevine payload must contain a top-level list of records"
                )
        else:
            raise ProviderPayloadError("Unsupported Filevine payload shape")

        if not all(isinstance(record, dict) for record in records):
            raise ProviderPayloadError("Filevine records must be objects")

        return records

    def _iter_org_candidates(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            candidates = []
            if any(key in payload for key in ("user_id", "userId", "org_id", "orgId")):
                candidates.append(payload)
            for key in ("data", "records", "items", "orgs", "organizations"):
                value = payload.get(key)
                if isinstance(value, list):
                    candidates.extend(item for item in value if isinstance(item, dict))
            return candidates
        return []

    def _latest_updated_at(self, records: list[dict[str, Any]]) -> datetime | None:
        latest_seen = None
        for record in records:
            project = record.get("project")
            if isinstance(project, dict):
                raw_value = project.get("last_activity_at") or project.get("updated_at")
            else:
                raw_value = record.get("updated_at")
            parsed = self._parse_expires_at(raw_value)
            if parsed is None:
                continue
            if latest_seen is None or parsed > latest_seen:
                latest_seen = parsed
        return latest_seen

    def _parse_expires_at(self, raw_value: Any) -> datetime | None:
        if not isinstance(raw_value, str) or not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
