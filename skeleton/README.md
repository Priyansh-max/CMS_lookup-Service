# CMS Integration Layer

## Setup

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in the values you want to use.

## Running

```bash
python -m src.main
```

The API will start on `http://127.0.0.1:8000`.

## Endpoints

- `GET /health`
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

## Design Decisions

Document your design decisions in `docs/design.md`.
