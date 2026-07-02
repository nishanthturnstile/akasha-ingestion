from __future__ import annotations

from akasha.config import Settings
from akasha.jobs.idempotency import compute_sync_idempotency_key
from akasha.jobs.store import Job
from akasha.providers.mock import MockProvider
from akasha.schemas import SyncRequest


class MockIngestionService:
    def __init__(
        self,
        *,
        job_store,
        object_store,
        provider: MockProvider,
        settings: Settings,
    ) -> None:
        self._job_store = job_store
        self._object_store = object_store
        self._provider = provider
        self._settings = settings

    def start_mock_sync(self, request: SyncRequest) -> Job:
        idempotency_key = compute_sync_idempotency_key(
            source_id=request.source_id,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
            job_type=request.job_type,
            request_params_version=self._settings.request_params_version,
            processing_profile_version=self._settings.processing_profile_version,
        )
        job, created = self._job_store.create_or_get(
            job_type=request.job_type,
            idempotency_key=idempotency_key,
            source_id=request.source_id,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
        )
        if not created:
            return job

        if self._settings.task_always_eager:
            return self.execute_mock_sync(job.job_id)

        from akasha.jobs.celery_app import celery_app

        try:
            celery_app.send_task("akasha.jobs.tasks.mock_sync", args=[job.job_id])
        except Exception as exc:
            self._job_store.mark_failed(job, error=f"task dispatch failed: {exc}")
            raise
        return job

    def execute_mock_sync(self, job_id: str) -> Job:
        job = self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        try:
            self._job_store.mark_running(job)
            scene = self._provider.search(
                source_id=job.source_id,
                date_start=job.date_start,
                date_end=job.date_end,
            )
            payload = self._provider.package_bytes(scene)
            object_path, checksum = self._object_store.put_raw_package(
                provider=scene.provider,
                source_id=scene.source_id,
                product_id=scene.product_id,
                payload=payload,
            )
            return self._job_store.mark_completed(
                job,
                object_path=object_path,
                checksum_sha256=checksum,
            )
        except Exception as exc:
            self._job_store.mark_failed(job, error=str(exc))
            raise
