"""PostgreSQL database setup and ORM table definitions for the repository layer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base ORM model."""


class FirmTable(Base):
    """Tenant-level firm configuration."""

    __tablename__ = "firms"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    provider_credentials: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CaseTable(Base):
    """Canonical case storage."""

    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "provider",
            "external_case_id",
            name="uq_case_provider_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    external_case_id: Mapped[str] = mapped_column(String(255))
    client_name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_client_name: Mapped[str] = mapped_column(String(255), index=True)
    client_phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    case_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_staff: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class SyncStateTable(Base):
    """Provider-specific sync state."""

    __tablename__ = "sync_state"
    __table_args__ = (
        UniqueConstraint("firm_id", "provider", name="uq_sync_state_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class FieldMappingTable(Base):
    """Per-firm field mapping overrides."""

    __tablename__ = "field_mappings"
    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "provider",
            "canonical_field",
            name="uq_field_mapping_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    canonical_field: Mapped[str] = mapped_column(String(100), index=True)
    source_fields: Mapped[list[str]] = mapped_column(JSON, default=list)


def normalize_database_url(database_url: str) -> str:
    """Normalize Postgres URLs so SQLAlchemy always uses the async driver."""

    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    raise ValueError("DATABASE_URL must be a PostgreSQL URL")


def create_engine_and_sessionmaker(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create the async PostgreSQL engine and session factory used by the repository."""

    engine = create_async_engine(normalize_database_url(database_url), future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory
