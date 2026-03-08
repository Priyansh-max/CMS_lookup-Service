"""Clio CMS Provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from src.providers.base import (
    CaseManagementProvider,
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderSyncResult,
    ProviderSyncState,
    ProviderTemporaryError,
)


class ClioProvider(CaseManagementProvider):
    """Fetch raw case payloads from the Clio API."""

    def __init__(
        self,
        *,
        api_base_url: str = "https://app.clio.com/api/v4",
        oauth_authorize_url: str = "https://app.clio.com/oauth/authorize",
        oauth_token_url: str = "https://app.clio.com/oauth/token",
        client_id: str = os.getenv("CLIO_CLIENT_ID"),
        client_secret: str = os.getenv("CLIO_CLIENT_SECRET"),
        redirect_uri: str = os.getenv("CLIO_REDIRECT_URI"),
        scopes: list[str] = [
            scope.strip()
            for scope in os.getenv("CLIO_SCOPES", "").split(",")
            if scope.strip()
        ],
        timeout_seconds: float = 15.0,
        refresh_buffer_seconds: int = 60,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.oauth_authorize_url = oauth_authorize_url
        self.oauth_token_url = oauth_token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes or []
        self.timeout_seconds = timeout_seconds
        self.refresh_buffer_seconds = refresh_buffer_seconds

    @property
    def provider_name(self) -> str:
        return "clio"

    def supports_oauth(self) -> bool:
        return True

    def build_authorize_url(self, *, state: str) -> str:
        if not self.client_id or not self.redirect_uri:
            raise ProviderConfigurationError(
                "Missing Clio client_id or redirect_uri for OAuth start"
            )

        scope_part = ""
        if self.scopes:
            scope_part = f"&scope={quote(' '.join(self.scopes))}"

        return (
            f"{self.oauth_authorize_url}"
            f"?response_type=code"
            f"&client_id={quote(self.client_id)}"
            f"&redirect_uri={quote(self.redirect_uri)}"
            f"&state={quote(state)}"
            f"{scope_part}"
        )

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise ProviderConfigurationError(
                "Missing Clio client credentials or redirect_uri for token exchange"
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.oauth_token_url,
                    json={
                        "grant_type": "authorization_code",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": self.redirect_uri,
                        "code": code,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while exchanging Clio auth code") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {400, 401, 403}:
                raise ProviderConfigurationError("Clio authorization code exchange failed") from exc
            raise ProviderTemporaryError(
                f"Clio token exchange failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Clio token exchange request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Clio token exchange returned invalid JSON") from exc

    def build_integration_credentials(
        self,
        token_response: dict[str, Any],
    ) -> dict[str, Any]:
        access_token = token_response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderPayloadError("Clio token response missing access_token")

        expires_in = token_response.get("expires_in")
        token_expires_at = None
        if isinstance(expires_in, (int, float)):
            token_expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))
            ).isoformat()

        credentials = {
            "access_token": access_token,
            "refresh_token": token_response.get("refresh_token"),
            "token_type": token_response.get("token_type", "Bearer"),
        }
        if "scope" in token_response:
            credentials["scope"] = token_response["scope"]
        if token_expires_at:
            credentials["token_expires_at"] = token_expires_at
        return credentials

    def credentials_need_refresh(self, credentials: dict[str, Any]) -> bool:
        """Refresh when the token is missing or close to its expiry window."""

        if not credentials.get("refresh_token"):
            return False
        if not credentials.get("access_token"):
            return True

        expires_at = self._parse_expires_at(credentials.get("token_expires_at"))
        if expires_at is None:
            return False

        refresh_at = expires_at - timedelta(seconds=self.refresh_buffer_seconds)
        return datetime.now(tz=timezone.utc) >= refresh_at

    async def refresh_access_token(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """Use the stored refresh token to obtain a fresh Clio access token."""

        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise ProviderConfigurationError("Missing Clio refresh token")
        if not self.client_id or not self.client_secret:
            raise ProviderConfigurationError(
                "Missing Clio client credentials for token refresh"
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.oauth_token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while refreshing Clio token") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {400, 401, 403}:
                raise ProviderConfigurationError("Clio refresh token is invalid or expired") from exc
            raise ProviderTemporaryError(
                f"Clio token refresh failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Clio token refresh request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Clio token refresh returned invalid JSON") from exc

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderPayloadError("Clio token refresh missing access_token")

        refreshed_credentials = dict(credentials)
        refreshed_credentials.update(self.build_integration_credentials(payload))
        refreshed_credentials["refresh_token"] = payload.get("refresh_token", refresh_token)
        refreshed_credentials["refreshed_at"] = datetime.now(tz=timezone.utc).isoformat()
        return refreshed_credentials

    async def sync_cases(
        self,
        *,
        firm_id: str,
        credentials: dict[str, Any],
        sync_state: ProviderSyncState | None = None,
    ) -> ProviderSyncResult:
        access_token = credentials.get("access_token")
        if not access_token:
            raise ProviderConfigurationError(
                f"Missing Clio access token for firm {firm_id}"
            )

        params = self._build_query_params(sync_state)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        records: list[dict[str, Any]] = []
        next_page_token = sync_state.page_token if sync_state else None

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                while True:
                    if next_page_token:
                        params["page_token"] = next_page_token
                    response = await client.get(
                        f"{self.api_base_url}/matters",
                        params=params,
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload = response.json()

                    page_records, next_page_token = self._parse_response(payload)
                    records.extend(page_records)

                    if not next_page_token:
                        break
        except httpx.TimeoutException as exc:
            raise ProviderTemporaryError("Timed out while calling Clio") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                raise ProviderConfigurationError(
                    f"Clio rejected credentials for firm {firm_id}"
                ) from exc
            raise ProviderTemporaryError(
                f"Clio request failed with status {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTemporaryError("Clio request failed") from exc
        except ValueError as exc:
            raise ProviderPayloadError("Clio returned invalid JSON") from exc

        return ProviderSyncResult(
            records=records,
            next_state=ProviderSyncState(
                since=self._latest_updated_at(records, sync_state),
                metadata={"strategy": "timestamp"},
            ),
            is_snapshot=False,
        )

    def _build_query_params(
        self,
        sync_state: ProviderSyncState | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 200}
        if sync_state and sync_state.since:
            params["updated_since"] = sync_state.since.isoformat()
        return params

    def _parse_response(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not isinstance(payload, dict):
            raise ProviderPayloadError("Clio response must be an object")

        raw_records = payload.get("data")
        if raw_records is None or not isinstance(raw_records, list):
            raise ProviderPayloadError("Clio response missing data list")

        next_page_token = None
        meta = payload.get("meta")
        if isinstance(meta, dict):
            paging = meta.get("paging")
            if isinstance(paging, dict):
                next_page_token = paging.get("next")

        return raw_records, next_page_token

    def _latest_updated_at(
        self,
        records: list[dict[str, Any]],
        sync_state: ProviderSyncState | None,
    ) -> datetime | None:
        latest_seen = sync_state.since if sync_state else None
        for record in records:
            updated_at = record.get("updated_at")
            if not isinstance(updated_at, str):
                continue
            try:
                parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except ValueError:
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
