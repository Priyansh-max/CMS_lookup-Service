"""Abstract base class for CMS providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ProviderError(Exception):
    """Base exception for provider-related failures."""


class ProviderConfigurationError(ProviderError):
    """Raised when the provider is missing required configuration."""


class ProviderTemporaryError(ProviderError):
    """Raised when the provider failure may succeed on retry."""


class ProviderPayloadError(ProviderError):
    """Raised when a provider returns an unexpected response shape."""


@dataclass(slots=True)
class ProviderSyncState:
    """Flexible sync state because different providers expose different cursors."""

    since: datetime | None = None
    cursor: str | None = None
    page_token: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderSyncResult:
    """Raw provider output plus the next sync state to persist later."""

    records: list[dict[str, Any]]
    next_state: ProviderSyncState | None = None
    is_snapshot: bool = False


class CaseManagementProvider(ABC):
    """Contract for fetching raw provider records."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier."""

    @abstractmethod
    async def sync_cases(
        self,
        *,
        firm_id: str,
        credentials: dict[str, Any],
        sync_state: ProviderSyncState | None = None,
    ) -> ProviderSyncResult:
        """Fetch raw case records using provider-specific sync semantics."""

    def supports_oauth(self) -> bool:
        """Whether this provider supports the generic OAuth bootstrap API."""

        return False

    def build_authorize_url(self, *, state: str) -> str:
        """Return the provider authorization URL for the given state."""

        raise ProviderConfigurationError(
            f"Provider {self.provider_name} does not support OAuth bootstrap"
        )

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange an OAuth authorization code for provider tokens."""

        raise ProviderConfigurationError(
            f"Provider {self.provider_name} does not support OAuth bootstrap"
        )

    def build_integration_credentials(
        self,
        token_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert provider token response into stored integration credentials."""

        raise ProviderConfigurationError(
            f"Provider {self.provider_name} does not support OAuth bootstrap"
        )
