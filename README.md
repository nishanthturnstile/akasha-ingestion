# Akasha Ingestion

Akasha Ingestion is the self-hosted satellite ingestion, metadata catalog, object lake, processing, and serving backend for Akasha.

Phase 1 establishes the core platform foundation: FastAPI, Celery, Postgres/PostGIS/pgSTAC ownership, MinIO lake zones, mock-provider ingestion, TiTiler smoke integration, observability, backups, Docker Compose, Ansible, and CI.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

Run the API shell:

```powershell
$hash = .\.venv\Scripts\python -c "from akasha.security import hash_api_key; print(hash_api_key('dev-akasha-key'))"
$env:AKASHA_API_KEY_HASHES = "dev:$hash"
.\.venv\Scripts\python -m uvicorn akasha.api.app:app --reload
```

Phase 1 API routes are protected with `X-API-Key` except `/health`.
