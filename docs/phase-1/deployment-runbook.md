# Phase 1 Deployment Runbook

This runbook is for the standalone `akasha-ingestion` platform on the provider-whitelisted
`akasha-staging` VM. In the two-VM topology, the product app runs on `akasha-control` and calls this
API server-to-server; browsers must never call ingestion, MinIO, Postgres/pgSTAC, Redis, or TiTiler
directly.

## Prerequisites

1. Complete `docs/phase-1/phase0-handoff-checklist.md`.
2. Confirm `akasha-staging` is the provider-whitelisted VM and has the observed Azure
   `Standard_D4s_v4` capacity: 4 vCPU, ~16 GiB RAM, 256 GiB OS disk, and a 512 GiB data disk mounted
   at `/srv/akasha`.
3. Preserve rollback points before deployment: previous immutable image SHA, current env file,
   current compose files, and a Postgres backup if replacing an existing ingestion deployment.
4. Stop or move any legacy product-app containers/volumes that still use `/srv/akasha`.
5. Confirm Caddy/API host ports do not conflict with existing services and will not be broadly
   exposed to the public internet.

For Docker Desktop on Windows, prefer a Linux-side `AKASHA_DATA_ROOT` such as
`/srv/akasha-local` in the local env file. Relative Windows bind paths can make PostgreSQL
initialization stall on `initdb`.

## Data-root layout

Keep all provider downloads, raw rasters, work directories, derived COGs, composites, validation
scratch data, Postgres, Redis, MinIO, Caddy state, monitoring data, logs, and backups on the
`/srv/akasha` data disk. Do not place bulk ingestion data under `/`, `/tmp`, `/var/tmp`,
`/var/lib/docker`, or `/data/coolify`.

If legacy app data remains under `/srv/akasha`, choose a collision-free ingestion subroot such as
`/srv/akasha/ingestion-platform` and set `AKASHA_DATA_ROOT` to that path. Otherwise use the direct
layout below:

```text
/srv/akasha/postgres
/srv/akasha/redis
/srv/akasha/minio
/srv/akasha/scratch
/srv/akasha/data/raw
/srv/akasha/data/work
/srv/akasha/monitoring
/srv/akasha/backups
/srv/akasha/caddy
```

The compose file binds `${AKASHA_DATA_ROOT}/scratch` to the container scratch path. If you change
`AKASHA_SCRATCH_DIR`, keep it on the approved data root or verify it is still backed by that bind
mount.

## Environment file and secrets

Create a VM-local env file outside source control, for example `deploy/env/staging.env`, from
`deploy/env/dev.example.env`. Do not commit it.

Required staging values include:

```dotenv
AKASHA_ENVIRONMENT=staging
AKASHA_RUNTIME_BACKEND=external
AKASHA_DATA_ROOT=/srv/akasha
AKASHA_HTTP_PORT=8080
AKASHA_PUBLIC_BASE_URL=http://<private-ingestion-host-or-ip>
AKASHA_API_KEY_HASHES=<name:sha256-hex>
AKASHA_SIGNING_SECRET=<secret-from-vault>
POSTGRES_DB=akasha
POSTGRES_USER=<from-vault>
POSTGRES_PASSWORD=<from-vault>
MINIO_ROOT_USER=<from-vault>
MINIO_ROOT_PASSWORD=<from-vault>
AKASHA_MINIO_BUCKET=akasha-data
```

Store API keys only as hashes in `AKASHA_API_KEY_HASHES`; store the plaintext key only in the
product app deployment secret as `INGESTION_API_KEY`. Generate a hash without printing or committing
the plaintext value:

```bash
python -c "from getpass import getpass; from akasha.security import hash_api_key; print(hash_api_key(getpass('API key: ')))"
```

Set `AKASHA_PUBLIC_BASE_URL` to the exact private URL prefix that the product app BFF uses as
`INGESTION_API_URL`, including scheme, host, and port, with no trailing slash. A mismatch causes the
app BFF to reject returned signed URLs during prefix validation. For HTTPS private ingress, set both
values to the same `https://...` prefix and configure Caddy certificates; for HTTP staging, bind it
only on a private interface or restrict access to the app VM private IP.

## Private exposure

The Caddy edge should expose only `/health`, `/api/*`, and `/tiles/*` to the private app-to-ingestion
network. Internal MinIO, Postgres/pgSTAC, Redis, TiTiler, Prometheus, Grafana, and provider routes
must not be publicly reachable. Use private networking, VNet peering, WireGuard, or an
IP-allowlisted HTTPS endpoint, plus firewall/NSG rules that allow the `akasha-control` app VM and
operators only.

Do not point the browser or frontend code at this service. The supported product integration is:

1. Product app BFF calls `POST /api/v1/analytics/field-index` with `X-API-Key`.
2. Ingestion returns signed `statsUrl` and `overlayUrl` values prefixed by `AKASHA_PUBLIC_BASE_URL`.
3. Product app BFF fetches the signed `overlayUrl` server-side and returns a same-origin,
   field-clipped PNG to the browser.

The signed overlay endpoint must return `image/png` and `X-Akasha-Overlay-Corners`.

## Deploy

Run init jobs explicitly; they are under the `tools` profile and are not run by a plain
`docker compose up -d`.

```bash
cd /srv/akasha/app/deploy
docker compose --env-file env/staging.env -f docker-compose.yml -f compose.prod.yml --profile tools run --rm pgstac-migrate
docker compose --env-file env/staging.env -f docker-compose.yml -f compose.prod.yml --profile tools run --rm migrate
docker compose --env-file env/staging.env -f docker-compose.yml -f compose.prod.yml --profile tools run --rm seed
docker compose --env-file env/staging.env -f docker-compose.yml -f compose.prod.yml up -d
```

If a staging-specific override exists, include it in every command after `compose.prod.yml`.
Use immutable image SHAs for production-like deploys when supplied by CI/CD.

## Standard_D4s_v4 resource guardrails

`Standard_D4s_v4` is acceptable for bounded MVP ingestion, but it is not sized for concurrent heavy
backfills or composites.

- Keep heavy worker concurrency at `1`.
- Avoid running concurrent heavy backfills, composites, and validation jobs.
- Prefer a staging override that gates optional monitoring behind profiles, caps Prometheus/Loki
  retention, and limits `worker-heavy` CPU/memory.
- Configure host swap before the first heavy provider/composite run.
- Monitor CPU, memory, disk usage, and disk I/O during canaries.
- Scale up before larger AOIs, concurrent processing, or production retention beyond the MVP.

## Validate

From `akasha-staging`:

```bash
curl -fsS http://localhost:8080/health
curl -fsS -H "X-API-Key: $AKASHA_API_KEY" http://localhost:8080/api/v1/sources
```

From `akasha-control` or an allowed private network host, repeat the health and sources checks
against the exact `INGESTION_API_URL` prefix.

For the product integration canary:

1. Call `POST /api/v1/analytics/field-index` for a bounded test polygon and available date/source.
2. Confirm the response is `AVAILABLE` and returned `overlayUrl` starts with
   `AKASHA_PUBLIC_BASE_URL`.
3. Fetch the signed `overlayUrl` before expiry and confirm `Content-Type: image/png` plus
   `X-Akasha-Overlay-Corners`.
4. Confirm no response exposes MinIO paths, object keys, `s3://` URLs, provider hrefs, or signed
   provider URLs.

## Rollback

If deployment fails:

1. Stop new runtime containers with the same compose/env file used for deployment.
2. Restore the previous env file and compose files.
3. Redeploy the previous immutable image SHA, not a mutable tag.
4. If migrations or seed data must be reversed, restore the pre-deploy Postgres backup rather than
   hand-editing the database.
5. Keep old app volumes under a dated `/srv/akasha/legacy-app/` path until product-app acceptance is
   confirmed; do not delete rollback data during ingestion rollout.
6. Re-run the validation checks after rollback and record the restored image SHA and backup used.
