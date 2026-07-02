from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from uuid import uuid4


class JobStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class Job:
    job_id: str
    job_type: str
    idempotency_key: str
    status: JobStatus
    source_id: str
    aoi_id: str
    date_start: str
    date_end: str
    object_path: str | None = None
    checksum_sha256: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryJobStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._jobs: dict[str, Job] = {}
        self._by_idempotency: dict[str, str] = {}

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
        with self._lock:
            existing_id = self._by_idempotency.get(idempotency_key)
            if existing_id is not None:
                existing_job = self._jobs[existing_id]
                if existing_job.status != JobStatus.FAILED:
                    return existing_job, False

            job = Job(
                job_id=str(uuid4()),
                job_type=job_type,
                idempotency_key=idempotency_key,
                status=JobStatus.QUEUED,
                source_id=source_id,
                aoi_id=aoi_id,
                date_start=date_start,
                date_end=date_end,
            )
            self._jobs[job.job_id] = job
            self._by_idempotency[idempotency_key] = job.job_id
            return job, True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at)

    def mark_running(self, job: Job) -> Job:
        with self._lock:
            job.status = JobStatus.RUNNING
            job.updated_at = datetime.now(UTC)
            return job

    def mark_queued(self, job: Job) -> Job:
        with self._lock:
            job.status = JobStatus.QUEUED
            job.updated_at = datetime.now(UTC)
            return job

    def health_check(self) -> bool:
        return True

    def mark_completed(self, job: Job, *, object_path: str, checksum_sha256: str) -> Job:
        with self._lock:
            job.status = JobStatus.COMPLETED
            job.object_path = object_path
            job.checksum_sha256 = checksum_sha256
            job.updated_at = datetime.now(UTC)
            return job

    def mark_failed(self, job: Job, *, error: str) -> Job:
        with self._lock:
            job.status = JobStatus.FAILED
            job.error = error
            job.updated_at = datetime.now(UTC)
            if self._by_idempotency.get(job.idempotency_key) == job.job_id:
                del self._by_idempotency[job.idempotency_key]
            return job
