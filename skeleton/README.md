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

## Design Decisions

Document your design decisions in `docs/design.md`.
