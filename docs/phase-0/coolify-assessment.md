# Phase 0 Coolify Assessment

This note assesses whether the existing Coolify VM should be used for Akasha Ingestion Phase 0/Phase 1.

## Observed `akasha-control` state

| Item | Value |
| --- | --- |
| SSH alias | `akasha-control` |
| Hostname | `akasha-control` |
| OS | Ubuntu 24.04.4 LTS |
| Egress IP | `20.204.163.166` |
| Docker | `Docker version 29.5.3` |
| Docker Compose | `Docker Compose version v5.1.4` |
| Data disk | `/data`, 251 GB total, 238 GB available |

Running Coolify containers:

| Container | Image |
| --- | --- |
| `coolify` | `ghcr.io/coollabsio/coolify:4.1.2` |
| `coolify-proxy` | `traefik:v3.6` |
| `coolify-db` | `postgres:15-alpine` |
| `coolify-redis` | `redis:7-alpine` |
| `coolify-realtime` | `ghcr.io/coollabsio/coolify-realtime:1.0.16` |
| `coolify-sentinel` | `ghcr.io/coollabsio/sentinel:0.0.21` |

Observed public listeners include `22`, `80`, `443`, `6001`, `6002`, `8000`, and `8080`.

## Recommendation

Use Coolify for the separate frontend and simple web-service deployments, but do not make it the primary deployment mechanism for the Akasha Ingestion platform during Phase 0 or Phase 1.

Rationale:

- The roadmap explicitly treats the UI as a separate project.
- The ingestion platform is stateful and storage-heavy: PostGIS/pgSTAC, MinIO raw lake, Redis/RabbitMQ, Celery workers, TiTiler, backup/restore, and provider-specific queues.
- Phase 0 and Phase 1 need deterministic Docker Compose topology and VM-level disk layout so Azure dev and on-prem production stay equivalent.
- Bhoonidhi/NRSC validation depends on `akasha-staging` egress IP `20.219.3.35`, not `akasha-control` egress IP `20.204.163.166`.
- Coolify adds another orchestration layer that may obscure storage, backup, network isolation, and provider-egress assumptions for the ingestion stack.

## Where Coolify can help

Coolify is appropriate for:

1. Frontend application deployment.
2. Preview/staging deployments of the separate UI.
3. Possibly a lightweight public API gateway or docs site later, if it proxies to the ingestion API.
4. Managing non-stateful support services that do not own provider raw data or geospatial processing state.

## Where Coolify should not be used initially

Avoid using Coolify initially for:

1. PostGIS/pgSTAC catalog.
2. MinIO raw/extracted/ARD/derived storage.
3. Celery workers and heavy GDAL/ResourceSat processing.
4. Provider credential storage and Bhoonidhi validation.
5. Backup/restore-critical services.
6. Phase 0 sample downloads and product inspection.

## Integration option for later phases

The clean boundary is:

```text
Coolify VM / frontend
  -> calls authenticated Akasha Ingestion API
  -> receives signed tile/stat URLs

Akasha staging/production VM
  -> runs Docker Compose ingestion platform
  -> owns provider credentials, raw lake, catalog, workers, TiTiler
```

If Coolify is used as a reverse-proxy entry point later, keep the ingestion platform's internal services private and expose only the authenticated API and approved tile/stat endpoints.

