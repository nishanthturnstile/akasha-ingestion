# Phase 1 Deployment Runbook

## Prerequisites

1. Complete `docs/phase-1/phase0-handoff-checklist.md`.
2. Copy `deploy/env/dev.example.env` to a VM-local env file outside source control.
3. Set `AKASHA_API_KEY_HASHES` using `akasha.security.hash_api_key`.
4. Confirm public ingress ports do not conflict with existing services.

For Docker Desktop on Windows, prefer a Linux-side `AKASHA_DATA_ROOT` such as
`/srv/akasha-local` in the local env file. Relative Windows bind paths can make
PostgreSQL initialization stall on `initdb`.

## Deploy

```bash
cd /srv/akasha/app/deploy
docker compose --env-file env/dev.env -f docker-compose.yml --profile tools run --rm pgstac-migrate
docker compose --env-file env/dev.env -f docker-compose.yml --profile tools run --rm migrate
docker compose --env-file env/dev.env -f docker-compose.yml --profile tools run --rm seed
docker compose --env-file env/dev.env -f docker-compose.yml -f compose.dev.yml up -d --build
```

## Validate

```bash
curl -fsS http://localhost:8080/health
curl -fsS -H "X-API-Key: $AKASHA_API_KEY" http://localhost:8080/api/v1/sources
```

Internal services must not be exposed publicly. Use firewall and NSG checks before running with real provider credentials.
