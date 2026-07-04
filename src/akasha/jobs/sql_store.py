from __future__ import annotations

from json import dumps, loads
from typing import Any

from sqlalchemy import Engine, text

from akasha.jobs.store import Job, JobStatus


def _loads_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return loads(value)
    return dict(value)


def _row_to_job(row: Any) -> Job:
    params = _loads_json(row.request_params)
    result = _loads_json(row.result_metadata)
    return Job(
        job_id=str(row.id),
        job_type=row.job_type,
        idempotency_key=row.idempotency_key,
        status=JobStatus(row.status),
        source_id=row.source_id or params.get("source_id", ""),
        aoi_id=row.aoi_id or params.get("aoi_id", ""),
        date_start=params.get("date_start", ""),
        date_end=params.get("date_end", ""),
        object_path=result.get("object_path"),
        checksum_sha256=result.get("checksum_sha256"),
        result_metadata=_public_result_metadata(result),
        error=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


class PostgresJobStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_or_get(
        self,
        *,
        job_type: str,
        idempotency_key: str,
        source_id: str,
        aoi_id: str,
        date_start: str,
        date_end: str,
    ) -> tuple[Job, bool]:
        params = {
            "source_id": source_id,
            "aoi_id": aoi_id,
            "date_start": date_start,
            "date_end": date_end,
        }
        with self._engine.begin() as connection:
            existing = _get_active_job_by_idempotency(connection, idempotency_key)
            if existing:
                return _row_to_job(existing), False

            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.processing_jobs (
                        job_type,
                        status,
                        idempotency_key,
                        source_id,
                        aoi_id,
                        request_params,
                        queued_at
                    )
                    VALUES (
                        :job_type,
                        'queued',
                        :idempotency_key,
                        :source_id,
                        :aoi_id,
                        CAST(:request_params AS jsonb),
                        now()
                    )
                    ON CONFLICT (idempotency_key)
                    WHERE status IN ('pending', 'queued', 'running', 'completed')
                    DO NOTHING
                    RETURNING *
                    """
                ),
                {
                    "job_type": job_type,
                    "idempotency_key": idempotency_key,
                    "source_id": source_id,
                    "aoi_id": aoi_id,
                    "request_params": dumps(params),
                },
            ).mappings().first()
            if row:
                return _row_to_job(row), True

            existing = _get_active_job_by_idempotency(connection, idempotency_key)
            if existing:
                return _row_to_job(existing), False

        raise RuntimeError("idempotent job conflict could not be resolved")

    def get(self, job_id: str) -> Job | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM akasha.processing_jobs WHERE id = CAST(:job_id AS uuid)"),
                {"job_id": job_id},
            ).mappings().first()
        return _row_to_job(row) if row else None

    def list(self) -> list[Job]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM akasha.processing_jobs ORDER BY created_at")
            ).mappings().all()
        return [_row_to_job(row) for row in rows]

    def mark_queued(self, job: Job) -> Job:
        return self._update_status(job.job_id, JobStatus.QUEUED)

    def mark_running(self, job: Job) -> Job:
        return self._update_status(job.job_id, JobStatus.RUNNING, started=True)

    def mark_completed(
        self,
        job: Job,
        *,
        object_path: str | None = None,
        checksum_sha256: str | None = None,
        result_metadata: dict[str, object] | None = None,
    ) -> Job:
        result = {"object_path": object_path, "checksum_sha256": checksum_sha256}
        if result_metadata:
            result.update(result_metadata)
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE akasha.processing_jobs
                    SET status = 'completed',
                        result_metadata = CAST(:result_metadata AS jsonb),
                        completed_at = now(),
                        updated_at = now()
                    WHERE id = CAST(:job_id AS uuid)
                    RETURNING *
                    """
                ),
                {"job_id": job.job_id, "result_metadata": dumps(result)},
            ).mappings().one()
        return _row_to_job(row)

    def mark_failed(self, job: Job, *, error: str) -> Job:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE akasha.processing_jobs
                    SET status = 'failed',
                        error_message = :error,
                        completed_at = now(),
                        updated_at = now()
                    WHERE id = CAST(:job_id AS uuid)
                    RETURNING *
                    """
                ),
                {"job_id": job.job_id, "error": error},
            ).mappings().one()
        return _row_to_job(row)

    def _update_status(self, job_id: str, status: JobStatus, *, started: bool = False) -> Job:
        started_sql = ", started_at = now()" if started else ""
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    f"""
                    UPDATE akasha.processing_jobs
                    SET status = :status,
                        updated_at = now()
                        {started_sql}
                    WHERE id = CAST(:job_id AS uuid)
                    RETURNING *
                    """
                ),
                {"job_id": job_id, "status": status.value},
            ).mappings().one()
        return _row_to_job(row)

    def health_check(self) -> bool:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True


def _get_active_job_by_idempotency(connection: Any, idempotency_key: str) -> Any | None:
    return connection.execute(
        text(
            """
            SELECT *
            FROM akasha.processing_jobs
            WHERE idempotency_key = :idempotency_key
              AND status IN ('pending', 'queued', 'running', 'completed')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"idempotency_key": idempotency_key},
    ).mappings().first()


def _public_result_metadata(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"object_path", "checksum_sha256"}
    }
