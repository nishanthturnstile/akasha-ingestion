# Phase 1 Schema Notes

Phase 1 uses two ownership boundaries:

| Schema | Owner | Purpose |
| --- | --- | --- |
| `pgstac` | pgSTAC tooling | STAC-native catalog/search objects. |
| `akasha` | Alembic | Operational metadata, source registry, jobs, orders, assets, audit logs, and tile layer registry. |

Alembic migration `0001_core_platform` creates only the `akasha` operational schema plus required Postgres extensions. It does not manage pgSTAC internals.

Provider-specific metadata remains in JSONB extension fields until real Phase 2/3 products justify stronger columns.
