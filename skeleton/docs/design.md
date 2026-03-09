## Goal

Build a production-minded backend service that can connect one firm to multiple CMS providers, sync provider data into one canonical case model, store it in PostgreSQL, and serve fast local lookup for AI workflows.

## Final Current Implementation

### Implemented

- Layered backend with clear `providers`, `transformers`, `storage`, `sync`, and `api` boundaries
- `PostgreSQL` as the only runtime and test database path, accessed through `SQLAlchemy`
- Multi-provider firm model:
  - `firms` stores tenant identity
  - `firm_integrations` stores provider-specific credentials and integration state
- Provider support:
  - `Clio` with OAuth bootstrap, stored access/refresh tokens, and automatic refresh
  - `Filevine` with two ingestion modes:
    - local JSON snapshot mode for dev/demo
    - live PAT-based mode that exchanges PAT -> bearer token -> user/org context -> live API fetch
- Canonical `CaseRecord` transformation layer with provider defaults plus firm-level mapping overrides
- Shared sync engine that:
  - loads integration credentials from the database
  - refreshes provider credentials when required
  - fetches raw records
  - transforms to canonical shape
  - upserts cases
  - updates provider-specific sync state
- Manual sync API and in-process scheduled sync support
- Local lookup API with DB-backed candidate narrowing and app-side fuzzy ranking
- Provider, storage, sync, and API tests around the main paths

### Current API Bootstrap Shape

- `POST /auth/clio/bootstrap`
  - first call with `firm_id` returns `authorization_url`
  - second call with `firm_id` and `code` stores initial Clio credentials in `firm_integrations`
- `POST /auth/filevine/bootstrap`
  - stores the initial Filevine PAT in `firm_integrations`
  - later sync derives live runtime credentials from that PAT

## Current Architecture

- `providers/`
  - provider-specific auth, fetch logic, and refresh/exchange behavior
- `transformers/`
  - provider payload -> canonical case mapping
- `storage/`
  - PostgreSQL tables, repository methods, upserts, and sync state persistence
- `sync/`
  - orchestration and credential refresh before provider fetch
- `api/`
  - onboarding/bootstrap, sync trigger, field mapping, and lookup endpoints

## Canonical Model

Core case fields:

- `firm_id`
- `provider`
- `external_case_id`
- `client_name`
- `client_phone`
- `client_email`
- `case_status`
- `assigned_staff`
- `updated_at`

This keeps provider-specific payloads out of the rest of the application.

## Database Structure

### `firms`

- one row per tenant / business
- stores firm identity only

### `firm_integrations`

- one row per `firm_id + provider`
- stores provider-specific credentials/config
- current examples:
  - Clio: `access_token`, `refresh_token`, `token_expires_at`
  - Filevine: initial `pat`, then derived `access_token`, `user_id`, `org_id`, `token_expires_at`

### `cases`

- canonical case storage
- unique on `(firm_id, provider, external_case_id)`
- stores normalized search fields for lookup:
  - `normalized_client_name`
  - `normalized_client_phone`
  - `normalized_client_email`

### `sync_state`

- one row per `firm_id + provider`
- stores shared sync progress fields like `since`, `cursor`, `page_token`
- keeps flexible provider metadata in JSON

### `field_mappings`

- one row per `firm_id + provider + canonical_field`
- stores firm-specific mapping overrides

## Current Lookup Approach

Lookup is intentionally local-first.

- sync provider data into PostgreSQL first
- query PostgreSQL by `firm_id`
- use normalized indexed columns to narrow candidates
- prefer exact normalized match first
- then prefix / contains candidate narrowing in SQL
- finally apply `RapidFuzz` ranking in Python on the smaller candidate set

### DB-Level Optimization Already Added

- stronger normalization for names, phone numbers, and emails
- indexed normalized lookup columns in `cases`
- composite indexes for tenant-scoped name lookup
- updated-at index to keep fallback candidate selection cheap

This is the current practical step before moving to PostgreSQL-native fuzzy search.

## Provider-Specific Approach

### Clio

- bootstrap via authorization code exchange
- store initial OAuth credentials in `firm_integrations`
- later sync uses stored refresh token to renew access tokens automatically
- sync strategy is timestamp-style incremental where possible

### Filevine

- local JSON remains the easiest development/demo mode
- live mode uses:
  - firm PAT
  - app-level Filevine client ID / secret
  - bearer token exchange
  - org/user lookup
  - live project fetch
- this is not refresh-token based like Clio; it is PAT -> runtime credential derivation

## Field Mapping Approach

- every provider ships with provider-default mappings
- each firm can override those mappings through `field_mappings`
- sync loads those overrides and the transformer prefers them over defaults

This supports firm-specific nomenclature without changing core sync or lookup code.

## Tradeoffs

### 1. PostgreSQL + SQLAlchemy instead of raw SQL or a search engine

Why:
- keeps the storage layer maintainable
- gives relational integrity, async access, and enough indexing power for this stage

Tradeoff:
- not the absolute fastest search path compared to a dedicated search layer or heavy custom SQL
- acceptable because current scope still benefits more from maintainability and clean boundaries

### 2. Keep fuzzy ranking partly in Python instead of fully inside PostgreSQL

Why:
- easier to reason about and test
- avoids prematurely locking the design to Postgres-specific fuzzy search functions

Tradeoff:
- not the final maximum-performance lookup design
- current DB candidate narrowing reduces the practical cost enough for this stage

### 3. `firm_integrations` keeps credentials in JSON instead of strongly typed per-provider columns

Why:
- different providers need different credential shapes
- this keeps onboarding flexible and avoids schema churn for each new provider

Tradeoff:
- weaker DB-level validation of provider credential structure
- acceptable because provider code still validates required fields at runtime

### 4. One integration per `firm + provider`

Why:
- simplest model that fits the current requirements
- enough for one Clio connection and one Filevine connection per firm

Tradeoff:
- does not yet support multiple accounts of the same provider for one firm
- can be extended later if that requirement appears

### 5. Filevine keeps local JSON and live PAT modes together

Why:
- local JSON remains the fastest development/demo path
- live PAT path is now available for real onboarding later

Tradeoff:
- provider code is more complex because it supports two ingestion modes
- worth it because it preserves a usable demo path while enabling a real integration path

### 6. In-process scheduler instead of separate workers

Why:
- simple to operate in a take-home / early service stage
- enough for periodic sync demonstrations

Tradeoff:
- not ideal for high-volume production scheduling or distributed coordination
- later this should move to a separate worker/job system if sync load grows

### 7. Provider-specific bootstrap APIs instead of one generic onboarding API

Why:
- Clio and Filevine auth are genuinely different
- provider-specific endpoints are easier to understand and keep honest

Tradeoff:
- more route surface area as providers grow
- still the right choice because the underlying bootstrap semantics are not actually uniform

## What Is Still Intentionally Left Out

- migration tooling such as Alembic
- production observability stack
- self-serve onboarding UI for mappings and provider setup
- soft-delete / inactive lifecycle for missing snapshot records
- PostgreSQL-native fuzzy search with `pg_trgm`
- encryption / external secret manager for provider credentials at rest
- distributed worker architecture for sync execution