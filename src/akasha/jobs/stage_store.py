from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class JobStage:
    stage_id: str
    job_id: str
    stage_name: str
    attempt: int
    status: StageStatus
    error_code: str | None = None
    error_message: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryStageStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._stages: dict[str, JobStage] = {}

    def start_stage(
        self,
        *,
        job_id: str,
        stage_name: str,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobStage:
        with self._lock:
            if self._running_stage(job_id, stage_name) is not None:
                raise ValueError(f"stage already running: {job_id}:{stage_name}")
            attempt = self._next_attempt(job_id, stage_name)
            now = datetime.now(UTC)
            stage = JobStage(
                stage_id=str(uuid4()),
                job_id=job_id,
                stage_name=stage_name,
                attempt=attempt,
                status=StageStatus.RUNNING,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                metadata=metadata or {},
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            self._stages[stage.stage_id] = stage
            return stage

    def mark_completed(self, stage_id: str, *, metadata: dict[str, Any] | None = None) -> JobStage:
        with self._lock:
            stage = self._get_required(stage_id)
            stage.status = StageStatus.COMPLETED
            stage.completed_at = datetime.now(UTC)
            stage.updated_at = stage.completed_at
            if metadata:
                stage.metadata.update(metadata)
            return stage

    def mark_failed(
        self,
        stage_id: str,
        *,
        error_code: str,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> JobStage:
        with self._lock:
            stage = self._get_required(stage_id)
            stage.status = StageStatus.FAILED
            stage.error_code = error_code
            stage.error_message = error_message
            stage.completed_at = datetime.now(UTC)
            stage.updated_at = stage.completed_at
            if metadata:
                stage.metadata.update(metadata)
            return stage

    def list_for_job(self, job_id: str) -> list[JobStage]:
        with self._lock:
            stages = [stage for stage in self._stages.values() if stage.job_id == job_id]
        return sorted(stages, key=lambda stage: (stage.stage_name, stage.attempt))

    def _running_stage(self, job_id: str, stage_name: str) -> JobStage | None:
        return next(
            (
                stage
                for stage in self._stages.values()
                if stage.job_id == job_id
                and stage.stage_name == stage_name
                and stage.status == StageStatus.RUNNING
            ),
            None,
        )

    def _next_attempt(self, job_id: str, stage_name: str) -> int:
        attempts = [
            stage.attempt
            for stage in self._stages.values()
            if stage.job_id == job_id and stage.stage_name == stage_name
        ]
        return max(attempts, default=0) + 1

    def _get_required(self, stage_id: str) -> JobStage:
        stage = self._stages.get(stage_id)
        if stage is None:
            raise ValueError(f"stage not found: {stage_id}")
        return stage


class PostgresStageStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start_stage(
        self,
        *,
        job_id: str,
        stage_name: str,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobStage:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    WITH next_attempt AS (
                        SELECT COALESCE(MAX(attempt), 0) + 1 AS attempt
                        FROM akasha.processing_job_stages
                        WHERE job_id = CAST(:job_id AS uuid)
                          AND stage_name = :stage_name
                    )
                    INSERT INTO akasha.processing_job_stages (
                        job_id,
                        stage_name,
                        attempt,
                        status,
                        lease_owner,
                        lease_expires_at,
                        metadata,
                        started_at
                    )
                    SELECT
                        CAST(:job_id AS uuid),
                        :stage_name,
                        next_attempt.attempt,
                        'running',
                        :lease_owner,
                        :lease_expires_at,
                        CAST(:metadata AS jsonb),
                        now()
                    FROM next_attempt
                    RETURNING *
                    """
                ),
                {
                    "job_id": job_id,
                    "stage_name": stage_name,
                    "lease_owner": lease_owner,
                    "lease_expires_at": lease_expires_at,
                    "metadata": _json_dumps(metadata or {}),
                },
            ).mappings().one()
        return _row_to_stage(row)

    def mark_completed(self, stage_id: str, *, metadata: dict[str, Any] | None = None) -> JobStage:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE akasha.processing_job_stages
                    SET status = 'completed',
                        metadata = metadata || CAST(:metadata AS jsonb),
                        completed_at = now(),
                        updated_at = now()
                    WHERE id = CAST(:stage_id AS uuid)
                    RETURNING *
                    """
                ),
                {"stage_id": stage_id, "metadata": _json_dumps(metadata or {})},
            ).mappings().one()
        return _row_to_stage(row)

    def mark_failed(
        self,
        stage_id: str,
        *,
        error_code: str,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> JobStage:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE akasha.processing_job_stages
                    SET status = 'failed',
                        error_code = :error_code,
                        error_message = :error_message,
                        metadata = metadata || CAST(:metadata AS jsonb),
                        completed_at = now(),
                        updated_at = now()
                    WHERE id = CAST(:stage_id AS uuid)
                    RETURNING *
                    """
                ),
                {
                    "stage_id": stage_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "metadata": _json_dumps(metadata or {}),
                },
            ).mappings().one()
        return _row_to_stage(row)

    def list_for_job(self, job_id: str) -> list[JobStage]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.processing_job_stages
                    WHERE job_id = CAST(:job_id AS uuid)
                    ORDER BY stage_name, attempt
                    """
                ),
                {"job_id": job_id},
            ).mappings().all()
        return [_row_to_stage(row) for row in rows]


def _row_to_stage(row: Any) -> JobStage:
    return JobStage(
        stage_id=str(row.id),
        job_id=str(row.job_id),
        stage_name=row.stage_name,
        attempt=row.attempt,
        status=StageStatus(row.status),
        error_code=row.error_code,
        error_message=row.error_message,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        metadata=dict(row.metadata or {}),
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _json_dumps(value: dict[str, Any]) -> str:
    from json import dumps

    return dumps(value, sort_keys=True)
