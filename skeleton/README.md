# CMS Integration Layer

## Setup

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in the values you want to use.

The project is PostgreSQL-only for runtime and automated tests.

Example:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cms_integration
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cms_integration_test
```

`TEST_DATABASE_URL` should always point to a disposable test database because the test suite resets schema state.

## App-Level Provider Config

These values belong to the application, not to a specific firm:

```env
CLIO_CLIENT_ID=
CLIO_CLIENT_SECRET=
CLIO_REDIRECT_URI=
CLIO_SCOPES=

FILEVINE_CLIENT_ID=
FILEVINE_CLIENT_SECRET=
FILEVINE_SCOPES=
```

Optional provider URLs exist in code with defaults:

- Clio auth URL defaults to `https://app.clio.com/oauth/authorize`
- Clio token URL defaults to `https://app.clio.com/oauth/token`
- Clio API base URL defaults to `https://app.clio.com/api/v4`
- Filevine identity URL defaults to `https://identity.filevine.com/connect/token`
- Filevine org lookup URL defaults to `https://api.filevineapp.com/fv-app/v2/utils/GetUserOrgsWithToken`
- Filevine projects URL defaults to `https://api.filevineapp.com/fv-app/v2/Projects`

## Running

```bash
python -m src.main
```

The API starts on `http://127.0.0.1:8000`.

## Running Tests

```bash
python -m pytest
```

The test suite requires `TEST_DATABASE_URL`.

## API Endpoints

- `GET /health`
- `POST /auth/clio/bootstrap`
- `POST /auth/filevine/bootstrap`
- `GET /firms`
- `POST /firms`
- `GET /firms/{firm_id}/integrations`
- `POST /firms/{firm_id}/integrations`
- `GET /cases/lookup?firm_id=<firm_id>&name=<client_name>`
- `POST /sync`
- `POST /firms/{firm_id}/mapping`

## Firm Bootstrap Flow

Create the firm first:

```json
{
  "firm_id": "firm-1",
  "name": "Firm One"
}
```

### Clio

Phase 1: get the Clio authorization URL

```json
{
  "firm_id": "firm-1"
}
```

Send that to `POST /auth/clio/bootstrap`.

The response includes `authorization_url`.

Phase 2: after approval, exchange the returned code and store initial credentials

```json
{
  "firm_id": "firm-1",
  "code": "authorization-code-from-clio"
}
```

That stores the initial Clio integration credentials in `firm_integrations`, including values like:

```json
{
  "access_token": "initial-access-token",
  "refresh_token": "refresh-token",
  "token_expires_at": "2026-03-08T12:00:00+00:00"
}
```

Later sync runs automatically refresh Clio credentials from the stored `refresh_token`.

### Filevine

Bootstrap Filevine by storing the firm PAT:

```json
{
  "firm_id": "firm-1",
  "pat": "filevine-personal-access-token"
}
```

Send that to `POST /auth/filevine/bootstrap`.

That stores the initial Filevine integration credentials in `firm_integrations`:

```json
{
  "pat": "filevine-personal-access-token"
}
```

Later sync runs use that PAT plus app-level Filevine client credentials to derive and persist:

- `access_token`
- `user_id`
- `org_id`
- `token_expires_at`
- other runtime metadata

## Sync Modes

### Filevine local JSON mode

This remains the primary dev/demo path.

You can still sync with an explicit payload like:

```json
{
  "requests": [
    {
      "firm_id": "firm-filevine",
      "provider": "filevine",
      "credentials": {
        "sample_path": "D:/path/to/filevine_cases.json"
      }
    }
  ]
}
```

### Integration-backed sync

If credentials are already stored in `firm_integrations`, you can sync without sending them again:

```json
{
  "requests": [
    {
      "firm_id": "firm-1",
      "provider": "clio"
    },
    {
      "firm_id": "firm-1",
      "provider": "filevine"
    }
  ]
}
```

## Field Mapping Overrides

Provider transformers ship with provider-level default mappings.

If a specific firm uses different field names for a provider, save firm-specific overrides through:

- `POST /firms/{firm_id}/mapping`

Example:

```json
{
  "provider": "clio",
  "mappings": {
    "client_name": ["display_name"],
    "case_status": ["state"]
  }
}
```

During sync, these overrides are preferred over provider defaults.

## Design Notes

- `firms` stores tenant identity
- `firm_integrations` stores provider-specific credentials/config
- `cases`, `sync_state`, and `field_mappings` are still keyed by `firm_id + provider`

See `docs/design.md` for the broader architecture and tradeoff notes.
