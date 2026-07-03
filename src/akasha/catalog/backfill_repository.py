from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from json import dumps
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text


@dataclass(slots=True)
class BackfillRunRecord:
    id: str | None
    job_id: str
    source_id: str
    aoi_id: str
    date_start: date
    date_end: date
    status: str = "running"
    searched_count: int = 0
    accepted_count: int = 0
    mirrored_asset_count: int = 0
    skipped_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    retryable_failed_count: int = 0
    terminal_failed_count: int = 0
    estimated_source_mirror_bytes: int | None = None
    actual_source_mirror_bytes: int | None = None
    summary_json: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InMemoryBackfillRepository:
    def __init__(self) -> None:
        self._runs: dict[str, BackfillRunRecord] = {}

    def upsert(self, run: BackfillRunRecord) -> BackfillRunRecord:
        run.id = run.id or str(uuid4())
        now = datetime.now(UTC)
        run.created_at = run.created_at or now
        run.updated_at = now
        self._runs[run.id] = run
        return run

    def get_by_job(self, job_id: str) -> BackfillRunRecord | None:
        return next((run for run in self._runs.values() if run.job_id == job_id), None)


class DatabaseBackfillRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, run: BackfillRunRecord) -> BackfillRunRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.backfill_runs (
                        job_id,
                        source_id,
                        aoi_id,
                        date_start,
                        date_end,
                        status,
                        searched_count,
                        accepted_count,
                        mirrored_asset_count,
                        skipped_count,
                        processed_count,
                        failed_count,
                        retryable_failed_count,
                        terminal_failed_count,
                        estimated_source_mirror_bytes,
                        actual_source_mirror_bytes,
                        summary_json,
                        started_at,
                        completed_at
                    )
                    VALUES (
                        CAST(:job_id AS uuid),
                        :source_id,
                        :aoi_id,
                        :date_start,
                        :date_end,
                        :status,
                        :searched_count,
                        :accepted_count,
                        :mirrored_asset_count,
                        :skipped_count,
                        :processed_count,
                        :failed_count,
                        :retryable_failed_count,
                        :terminal_failed_count,
                        :estimated_source_mirror_bytes,
                        :actual_source_mirror_bytes,
                        CAST(:summary_json AS jsonb),
                        :started_at,
                        :completed_at
                    )
                    ON CONFLICT (source_id, aoi_id, date_start, date_end, job_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        searched_count = EXCLUDED.searched_count,
                        accepted_count = EXCLUDED.accepted_count,
                        mirrored_asset_count = EXCLUDED.mirrored_asset_count,
                        skipped_count = EXCLUDED.skipped_count,
                        processed_count = EXCLUDED.processed_count,
                        failed_count = EXCLUDED.failed_count,
                        retryable_failed_count = EXCLUDED.retryable_failed_count,
                        terminal_failed_count = EXCLUDED.terminal_failed_count,
                        estimated_source_mirror_bytes = EXCLUDED.estimated_source_mirror_bytes,
                        actual_source_mirror_bytes = EXCLUDED.actual_source_mirror_bytes,
                        summary_json = EXCLUDED.summary_json,
                        started_at = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at,
                        updated_at = now()
                    RETURNING *
                    """
                ),
                _backfill_params(run),
            ).mappings().one()
        return _row_to_backfill(row)

    def get_by_job(self, job_id: str) -> BackfillRunRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM akasha.backfill_runs WHERE job_id = CAST(:job_id AS uuid)"),
                {"job_id": job_id},
            ).mappings().first()
        return _row_to_backfill(row) if row else None


def _backfill_params(run: BackfillRunRecord) -> dict[str, Any]:
    return {
        "job_id": run.job_id,
        "source_id": run.source_id,
        "aoi_id": run.aoi_id,
        "date_start": run.date_start,
        "date_end": run.date_end,
        "status": run.status,
        "searched_count": run.searched_count,
        "accepted_count": run.accepted_count,
        "mirrored_asset_count": run.mirrored_asset_count,
        "skipped_count": run.skipped_count,
        "processed_count": run.processed_count,
        "failed_count": run.failed_count,
        "retryable_failed_count": run.retryable_failed_count,
        "terminal_failed_count": run.terminal_failed_count,
        "estimated_source_mirror_bytes": run.estimated_source_mirror_bytes,
        "actual_source_mirror_bytes": run.actual_source_mirror_bytes,
        "summary_json": dumps(run.summary_json, sort_keys=True),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def _row_to_backfill(row: Any) -> BackfillRunRecord:
    return BackfillRunRecord(
        id=str(row.id),
        job_id=str(row.job_id),
        source_id=row.source_id,
        aoi_id=row.aoi_id,
        date_start=row.date_start,
        date_end=row.date_end,
        status=row.status,
        searched_count=row.searched_count,
        accepted_count=row.accepted_count,
        mirrored_asset_count=row.mirrored_asset_count,
        skipped_count=row.skipped_count,
        processed_count=row.processed_count,
        failed_count=row.failed_count,
        retryable_failed_count=row.retryable_failed_count,
        terminal_failed_count=row.terminal_failed_count,
        estimated_source_mirror_bytes=row.estimated_source_mirror_bytes,
        actual_source_mirror_bytes=row.actual_source_mirror_bytes,
        summary_json=dict(row.summary_json or {}),
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
