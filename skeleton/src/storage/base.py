"""Abstract storage interface."""

from abc import ABC, abstractmethod

from src.models.canonical import (
    CaseRecord,
    CaseSearchQuery,
    FieldMappingRecord,
    FirmRecord,
    StoredSyncState,
)


class CaseRepository(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the storage backend."""

    @abstractmethod
    async def save_firm(self, firm: FirmRecord) -> None:
        """Insert or update one firm record."""

    @abstractmethod
    async def get_firm(self, firm_id: str) -> FirmRecord | None:
        """Load one firm by its identifier."""

    @abstractmethod
    async def list_firms(self) -> list[FirmRecord]:
        """Return all saved firms."""

    @abstractmethod
    async def save_case(self, case: CaseRecord) -> None:
        """Insert or update one canonical case record."""

    @abstractmethod
    async def save_cases(self, cases: list[CaseRecord]) -> None:
        """Insert or update many canonical case records."""

    @abstractmethod
    async def find_candidates_by_name(self, query: CaseSearchQuery) -> list[CaseRecord]:
        """Return tenant-scoped candidate records for lookup."""

    @abstractmethod
    async def get_case_by_external_id(
        self,
        firm_id: str,
        provider: str,
        external_case_id: str,
    ) -> CaseRecord | None:
        """Load one case by its provider identity."""

    @abstractmethod
    async def get_sync_state(self, firm_id: str, provider: str) -> StoredSyncState | None:
        """Return saved sync state for one firm/provider pair."""

    @abstractmethod
    async def upsert_sync_state(self, sync_state: StoredSyncState) -> None:
        """Insert or update provider-specific sync state."""

    @abstractmethod
    async def get_field_mappings(
        self,
        firm_id: str,
        provider: str,
    ) -> dict[str, list[str]]:
        """Return mapping overrides for a firm/provider pair."""

    @abstractmethod
    async def save_field_mappings(self, mappings: list[FieldMappingRecord]) -> None:
        """Insert or replace field mappings for a firm/provider pair."""
