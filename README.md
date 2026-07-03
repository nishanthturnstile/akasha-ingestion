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

## Phase 2 Sentinel-2 vertical slice

Phase 2 adds the Sentinel-2 Earth Search/AWS COG vertical slice: STAC/raster dependencies,
provider route metadata, durable stage tracking, source/derived asset schema extensions, profile
seeds, streaming source COG mirroring, derived index COG generation, pgSTAC registration helpers,
field-index `AVAILABLE` responses, and signed tile/stat resolver routes. Reinstall after pulling
these changes:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Live Earth Search/provider tests remain opt-in with `AKASHA_LIVE_PROVIDER_TESTS=true`. The default
Docker Compose stack keeps TiTiler-PgSTAC on the internal network; public tile/stat access goes
through API signed resolver routes.

Current Phase 2 checkpoint status:

- implemented and offline-tested: mocked Earth Search search, source STAC/asset manifest storage,
  source COG mirroring with byte limits, Sentinel-2 index COG generation, catalog/tile-layer
  registration, field-index `AVAILABLE`/`UNAVAILABLE` responses, signed stats resolver, signed tile
  resolver, and path/href non-disclosure;
- live validation remains opt-in and environment-dependent: Earth Search reachability, object-store
  headroom, Postgres/pgSTAC migration+seed, MinIO-backed pgSTAC item serving, and TiTiler-PgSTAC
  rendering should be verified in the target deployment.

Before live Phase 2F validation, operators must set a non-default `AKASHA_SIGNING_SECRET`, confirm
MinIO headroom for mirrored COGs, and opt in with `AKASHA_LIVE_PROVIDER_TESTS=true`.
