# CMS Integration Layer

## Setup

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in the values you want to use.

The system is now PostgreSQL-only for runtime, manual testing, and automated tests.
Create dedicated databases first and point the env vars to them, for example:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cms_integration
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cms_integration_test
MANUAL_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cms_integration_manual
```

`TEST_DATABASE_URL` should always point to a disposable test database because the test suite resets schema state.

For OAuth providers, keep app-level OAuth secrets in `.env` and store per-firm tokens in the integration record:

```env
CLIO_CLIENT_ID=your-clio-app-client-id
CLIO_CLIENT_SECRET=your-clio-app-client-secret
CLIO_REDIRECT_URI=http://127.0.0.1/oauth/callback
CLIO_AUTH_URL=https://app.clio.com/oauth/authorize
CLIO_TOKEN_URL=https://app.clio.com/oauth/token
CLIO_SCOPES=matters:read,contacts:read
```

## Running

```bash
python -m src.main
```

The API will start on `http://127.0.0.1:8000`.

## Running Tests

```bash
python -m pytest
```

The test suite requires `TEST_DATABASE_URL` and will fail fast if it is not set.

## Endpoints

- `GET /health`
- `GET /auth/{provider}/start?firm_id=<firm_id>`
- `GET /auth/{provider}/callback?code=<code>&state=<state>`
- `GET /firms`
- `POST /firms`
- `GET /firms/{firm_id}/integrations`
- `POST /firms/{firm_id}/integrations`
- `GET /cases/lookup?firm_id=<firm_id>&name=<client_name>`
- `POST /sync`
- `POST /firms/{firm_id}/mapping`

## Manual Sync Payload

`POST /sync` can either:

- use default sync requests configured in `.env`
- or accept an explicit request body like:

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

You can also store credentials on an integration first and then sync without including them in the request body:

```json
{
  "requests": [
    {
      "firm_id": "firm-filevine",
      "provider": "filevine"
    }
  ]
}
```

For an OAuth-connected provider like Clio, the integration credentials are stored in `firm_integrations` after the first successful callback:

```json
{
  "provider": "clio",
  "provider_credentials": {
    "access_token": "initial-access-token",
    "refresh_token": "refresh-token",
    "token_expires_at": "2026-03-08T12:00:00+00:00"
  }
}
```

During sync, the engine will refresh the Clio access token automatically when the token is expired or close to expiry, then persist the new token set back to the integration row.

## Generic OAuth Bootstrap

The API now exposes provider-agnostic OAuth route shapes:

- `GET /auth/{provider}/start?firm_id=<firm_id>`
- `GET /auth/{provider}/callback?code=<code>&state=<state>`

Today, only `clio` implements this OAuth capability. Other providers can reuse the same route shape later by implementing the same provider-side methods.

The intended bootstrap flow is:

1. Create the firm with `POST /firms`
2. Start OAuth with `GET /auth/{provider}/start?firm_id=<firm_id>`
3. Complete the provider callback with `GET /auth/{provider}/callback?...`
4. The callback stores the provider tokens in `firm_integrations`
5. Later `/sync` calls read credentials from `firm_integrations` instead of `.env`

## Design Decisions

Document your design decisions in `docs/design.md`.
