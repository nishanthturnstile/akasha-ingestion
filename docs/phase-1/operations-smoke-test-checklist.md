# Phase 1 Operations Smoke-Test Checklist

| Check | Expected result |
| --- | --- |
| API health | `/health` returns `ok`. |
| API auth | `/api/v1/sources` rejects missing or invalid `X-API-Key`. |
| Source registry | `/api/v1/sources` returns seeded Phase 1 sources. |
| Mock sync | `/api/v1/ingestion/sync` creates a completed mock job. |
| Idempotency | Repeating the same sync payload returns the same job ID. |
| Raw lake | Mock object path is under `raw/mock/...` and has a checksum. |
| Job visibility | `/api/v1/jobs/{jobId}` returns DB/job-store state. |
| TiTiler | Internal smoke COG returns metadata/tile response. |
| Public ports | Only approved edge ports are reachable from outside. |
| Logs | Synthetic secrets are redacted. |
| Metrics | Prometheus scrapes API metrics. |
| Alerts | Controlled API/backup failure triggers Alertmanager rule. |
| Alert delivery | Dev webhook receiver on `host.docker.internal:19093` receives or visibly rejects test alerts. |
| Restore | Bounded restore validates DB rows, objects, config, and service health. |
