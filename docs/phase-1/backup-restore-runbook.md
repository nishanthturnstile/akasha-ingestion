# Phase 1 Backup and Restore Runbook

## Scope

Phase 1 validates backup and restore using a bounded dataset:

- PostgreSQL metadata and migrations.
- One mock raw object.
- One TiTiler smoke COG fixture.
- Encrypted config/secrets.
- Compose and monitoring configuration.

## PostgreSQL

Use pgBackRest for full and incremental backups with WAL archiving when production RPO/RTO is defined.
The Phase 1 Compose profile wires the pgBackRest sidecar to PostgreSQL through the shared
`postgres-run` socket volume and a read-only mount of the PostgreSQL data directory.
The sidecar image is built locally from `docker/pgbackrest.Dockerfile` because pgBackRest does
not publish an official versioned Docker image.

Dev validation commands:

```powershell
docker compose -f deploy\docker-compose.yml -f deploy\compose.dev.yml --profile backup run --rm pgbackrest --stanza=akasha stanza-create
docker compose -f deploy\docker-compose.yml -f deploy\compose.dev.yml --profile backup run --rm pgbackrest --stanza=akasha backup --type=full --no-archive-check
```

The `--no-archive-check` flag is limited to the bounded Phase 1 dev drill. Before production
or incremental backups, enable PostgreSQL `archive_mode` and an `archive_command` that pushes
WAL through pgBackRest, then replace the dev-only backup command with `pgbackrest check` and
regular full/incremental backup commands.

Phase 1 restore evidence must show:

1. Database restored.
2. Alembic version table present.
3. Source registry and mock job rows readable.

## MinIO and config

Use restic, replication, or equivalent backup storage for the bounded Phase 1 object set and configuration files.

Restore evidence must show object checksum equality and service health after restart.
