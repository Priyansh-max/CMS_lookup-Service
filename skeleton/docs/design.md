## Goal

Build a production backend service that syncs case data from multiple CMS providers,
normalizes it into one schema, stores it locally, and exposes a fast lookup API
for AI voice agents making sure extendibility for new CMS later with changing the core logic

## Current Implementation Status

### What is implemented

- A layered architecture with separate provider, transformer, storage, sync, and API layers
- `Clio` provider support through direct API calls when a valid access token is available
- `Filevine` provider support through local JSON / snapshot-style ingestion
- Canonical case transformation into a shared `CaseRecord`
- Local persistence for canonical cases, sync state, and field mappings
- A sync engine that orchestrates provider fetch -> transform -> persist -> sync-state update
- Scheduled sync support through `APScheduler`
- A `lookup_by_name` API with exact match first and fuzzy fallback ranking
- Unit and integration-style tests covering the critical paths

### What is intentionally left out or simplified

- No persisted firm configuration table yet
- No OAuth token refresh flow or secure secret storage for Clio
- No self-serve UI for onboarding and mapping management, only a backend API
- No automatic mapping suggestion workflow
- No soft-delete / inactive record lifecycle yet for snapshot ingestion
- No production observability stack yet (structured logs, metrics, tracing, dashboards)
- No distributed job coordination or worker separation for scheduler execution
- No migration tooling such as Alembic yet

### What this means honestly

The current implementation is a strong backend foundation, not a fully finished production platform. The core architecture, data flow, sync model, and API shape are in place. The main missing pieces are the operational and onboarding capabilities that would be expected before real production rollout.

## Proposed Layers

5 layer approach

- `providers/`: fetch raw data from Clio through its API and treat Filevine as a simulated/local JSON-backed provider for this checkpoint

- `transformers/`: map provider payloads into one canonical case model

- `storage/`: save cases, sync state, and field mappings

- `sync/`: run scheduled/manual incremental syncs making sure we only sync the data that was updated after a recent sync

- `api/`: expose `lookup_by_name` and sync endpoints

## Canonical Case Shape

This is the general shape we would be considering so that later if new firms are added we would transform there data into this shape and map accordingly
this way the AI input data structure stays consistent (core logic)

Main fields for now:

- `firm_id`
- `provider`
- `external_case_id`
- `client_name`
- `client_phone`
- `client_email`
- `case_status`
- `assigned_staff`
- `updated_at`

## Current Approach

- Clio: direct provider integration through its public API. The adapter will be responsible for fetching raw case data and passing it to the transformer layer.

- Filevine: use a simulated/local JSON adapter for this project. Filevine has options like direct API access, Snowflake DataBridge, and export-based ingestion, but those paths are more constrained and not ideal for a fast, reliable checkpoint implementation right now.

    possible researcher in filevine:
    1. Direct API integration (if firm credentials are available)
    2. Snowflake DataBridge integration
    3. Export-based ingestion (CSV/ETL pipelines) using data connector for auto 
    syncing

    But all the above 3 methods have limitations mostly latency
    https://support.filevine.com/hc/en-us/articles/46857222439323-Data-Export-Options
    https://support.filevine.com/hc/en-us/articles/41499337170587-DataBridge

- Database: the implementation currently runs on `SQLite` through `SQLAlchemy`, while the target production database is still `PostgreSQL`.

  Why this is the current choice:
  - `SQLite` makes the system runnable immediately on any local machine with almost no setup friction
  - `SQLAlchemy` keeps the storage layer portable, so moving to PostgreSQL later does not require rewriting business logic
  - for this current implementation size, local correctness and fast iteration matter more than introducing database operational overhead too early

  Why PostgreSQL is still the production target:
  - stronger concurrency behavior
  - better support for real multi-tenant workloads
  - better operational fit for larger sync volume and long-term scaling
  - more robust indexing and query options for future growth

- Sync: the sync engine should be shared, but each provider should be allowed to define its own incremental ingestion strategy. For providers like Clio, that may be `last_synced_at`. For providers that do not expose reliable last-updated filtering, we should support snapshot-style ingestion with idempotent upserts.

- Lookup: exact match first, then fuzzy matching for poor call transcripts. This helps with names like `John Smith` vs `Jon Smyth`.

- Field mappings: each firm can map its provider-specific fields into the canonical model so onboarding new firms does not require changing core application logic. (advanced stage)

## Provider-Specific Sync Strategy

- The sync engine should not assume all providers support the same sync semantics.
- Each provider adapter should expose how it fetches new data: timestamp-based, cursor-based, export-based, or snapshot-based.
- `sync_state` should therefore be provider-specific and flexible enough to store more than just `last_synced_at`.
- In practice, sync state may later include fields like `cursor`, `page_token`, `last_export_id`, or snapshot metadata depending on the provider.

### Example

- `Clio`: likely timestamp-based incremental sync if the API supports updated filters cleanly.
- `Filevine`: may require scheduled export or snapshot ingestion if direct last-updated incremental sync is not available through the path we use.

## Filevine Ingestion If `updated_at` Is Not Available

If Filevine does not support clean incremental sync using `updated_at`, the system should fall back to periodic snapshot-style ingestion:

- fetch the latest available export or dataset
- identify records by a stable external/provider id
- upsert records into the canonical store
- detect changed records by comparing incoming payloads or derived canonical fields
- optionally mark missing records as inactive/stale depending on the provider semantics

This is slower than ideal incremental sync, but it is still a valid and production-realistic ingestion strategy when a provider does not expose better change tracking.

## Field Mapping Approach

- Every provider has default mappings from provider fields to canonical fields.
- Each firm can override those mappings when their field naming or structure differs.
- This keeps the canonical model stable while still allowing firm-specific onboarding.
- Adding new mappings should not require changes in lookup, sync orchestration, or storage logic.

## Database Structure Implemented

The current storage layer contains three tables:

### `cases`

- stores canonical case records after transformation
- unique identity is `(firm_id, provider, external_case_id)`
- also stores `normalized_client_name` to support fast lookup candidate filtering
- stores `raw_payload` for debugging and mapping verification

### `sync_state`

- stores provider-specific sync progress per `firm_id + provider`
- supports shared fields like `since`, `cursor`, and `page_token`
- also keeps provider-specific state in JSON metadata

### `field_mappings`

- stores override mappings per `firm_id + provider + canonical_field`
- allows firm-specific customization without changing transformer code

## Why There Are No Foreign Keys Right Now

This is a fair concern.

The current schema does not define foreign keys because the implementation does not yet have a persisted `firms` table or a proper firm configuration model. Right now:

- `firm_id` is treated as a tenant identifier passed in through sync/API requests
- provider configuration is injected at runtime rather than loaded from relational firm records

From a production perspective, this is not the final ideal shape.

The more complete production design would be:

- add a `firms` table
- store provider configuration or references to secrets securely
- add foreign keys from `cases`, `sync_state`, and `field_mappings` back to `firms`

Why it is still acceptable in the current implementation:

- it keeps the current architecture honest instead of inventing a weak or incomplete firm-config subsystem
- it allows the sync, transformation, storage, and API flows to be built and verified cleanly first
- the repository contract is already structured so a `firms` table can be introduced later without rewriting the core layer boundaries

## Making Lookup Fast For AI Agents

- Keep the lookup path simple: the API should hit already-synced local data, not call provider APIs during the live request.
- Query by `firm_id` first so we stay tenant-scoped and reduce the search space before fuzzy matching.
- Add indexes on the most common lookup fields such as `firm_id`, `client_name`, and provider/external ids.
- Do exact/normalized matching first and only run fuzzy matching on a smaller candidate set.
- Keep the response small and focused on the fields the AI agent needs immediately.
- If scale grows later, we can add Redis or a stronger search strategy.

## Primary Tech / Libraries (for now constantly looking for new and better alternatives)

- API framework: `FastAPI`
  Why: it is a strong fit for a typed Python backend, gives us quick route creation, and makes request/response models easy to manage.

- HTTP client: `httpx`
  Why: clean async support for provider integrations like Clio and good timeout/retry control.

- Database access: `SQLAlchemy`
  Why: gives us a clean repository layer, model definitions, and flexibility to stay structured as the project grows.

- Database: `PostgreSQL`
  Why: best fit for relational case data, sync state, filtering, and upsert-style writes. It is the most realistic primary data store for this system.

- Scheduler: `APScheduler`
  Why: easy way to run periodic sync jobs inside the service for now without introducing a more complex worker setup too early.

- Fuzzy matching: `RapidFuzz`
  Why: lightweight, fast, and good for handling noisy voice transcript inputs such as misspelled client names.

- Testing: `pytest`
  Why: standard Python testing setup and easy to use for both unit and integration tests.

## What Adding More Providers Requires

When adding another provider, the goal is that only provider-specific pieces need to be implemented:

- a new provider adapter for auth, fetch logic, pagination, and sync semantics
- a new transformer that maps the provider payload into the canonical model
- default field mappings for that provider
- provider-specific sync state handling if the provider uses cursors, exports, or snapshots instead of timestamps

The rest of the system should stay the same:

- sync orchestration
- storage/repository layer
- lookup API
- fuzzy matching flow
- scheduler
- logging and testing patterns

## Assignment Coverage

### Achieved

- Unified multi-provider backend architecture
- Two provider paths:
  - `Clio` direct API integration path
  - `Filevine` simulated snapshot ingestion path
- Incremental / provider-specific sync design
- Manual sync trigger through API
- Scheduled sync support
- Canonical schema and transformation layer
- Fast local lookup API with fuzzy matching
- Firm-specific field mapping storage and application path
- Unit and integration-style tests

### Partially achieved

- `Clio` is integrated structurally, but real runtime use still depends on valid OAuth access tokens
- `Filevine` approach is modeled honestly, but current implementation uses local JSON instead of a live export or API ingestion path
- self-serve field mapping exists as backend capability, but not as a full non-technical onboarding interface

### Not yet achieved

- Persisted firm onboarding / configuration model
- Human-friendly self-serve onboarding UI
- automatic mapping suggestions with human verification
- production-grade secret management and token refresh
- production-grade logging / observability / operations
- migration tooling and deployment hardening

## Testability

- Providers should be testable independently with mocked payloads or sample files.
- Transformers should be unit tested with raw provider payloads and expected canonical output.
- The sync engine should be testable by mocking provider responses and repository behavior.
- The lookup layer should be testable with seeded case data and fuzzy-match scenarios.
- End-to-end integration tests should validate that sync populates local storage and lookup returns the expected results.

## Edge Cases To Handle

- duplicate records across repeated sync runs
- partial provider failures during a batch sync
- missing or inconsistent provider fields
- provider pagination and rate limits
- stale or deleted records in snapshot-based ingestion
- false positives in fuzzy name matching
- tenant isolation so one firm never sees another firm’s cases

## How I Would Make This Production Ready

- Add structured logging around sync runs, provider failures, and lookup latency.
- Add retry and timeout handling for provider API calls.
- Make sync idempotent so repeated runs do not create duplicate case records.
- Validate all external/provider payloads before saving transformed data.
- Add database constraints and indexes to protect data quality and query speed.
- Add health checks and clear error responses for operational visibility.
- Add unit tests for each layer and integration tests for sync plus lookup flows.
- Keep provider-specific logic isolated so future CMS integrations do not affect the core system heavily.
- Move from SQLite to PostgreSQL for the real deployment environment.
- Add a `firms` table and foreign key constraints to enforce tenant data integrity.
- Introduce secret storage and OAuth token refresh handling for live provider integrations.
- Separate scheduler execution from the API process if sync volume or operational complexity grows.

## Why This Direction

This direction keeps the first version simple but still production-minded. The
main idea is to separate provider-specific logic from the rest of the system so
that adding another CMS later does not require rewriting lookup, sync, or
storage logic. It also fits the current skeleton well, which means we can move
incrementally, validate each layer as we build it, and avoid spending time on a
big design rewrite before delivering working functionality.

## How To Explain This Honestly In An Interview

The strongest honest explanation is:

- the architecture is production-oriented even though some infrastructure choices are simplified locally
- `SQLAlchemy` was used intentionally so the code stays portable between `SQLite` now and `PostgreSQL` later
- the current implementation proves the core backend concerns: provider abstraction, canonical transformation, persistence, sync orchestration, lookup, and mapping
- the main missing production pieces are operational hardening, secret management, and a proper firm configuration / onboarding layer

This does not make the system fake. It means the implementation is a real backend foundation with clearly identified next steps, rather than an over-claimed “fully production-ready” system.
