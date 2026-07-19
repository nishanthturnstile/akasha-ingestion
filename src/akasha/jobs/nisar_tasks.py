from __future__ import annotations

from datetime import UTC, datetime, timedelta

from akasha.config import get_settings
from akasha.jobs.celery_app import celery_app
from akasha.processing.nisar import NISAR_PROVIDER_ROUTE
from akasha.runtime import create_engine_if_needed, create_nisar_ingestion_service
from akasha.schemas import SyncRequest


@celery_app.task(name="akasha.jobs.nisar_tasks.scheduled_preload")
def scheduled_preload() -> dict[str, str]:
    settings = get_settings()
    engine = create_engine_if_needed(settings)
    service = create_nisar_ingestion_service(settings, engine)
    today = datetime.now(UTC).date()
    job = service.start_backfill(
        SyncRequest(
            source_id=settings.nisar_preload_source_id,
            provider_route=settings.nisar_preload_provider_route,
            aoi_id=settings.nisar_preload_aoi_id,
            date_start=today - timedelta(days=settings.nisar_preload_date_window_days),
            date_end=today,
            job_type="nisar_backfill",
            mode="full_pipeline",
        )
    )
    return {"job_id": job.job_id, "status": job.status.value}


@celery_app.task(bind=True, name="akasha.jobs.nisar_tasks.backfill")
def backfill(
    self,
    job_id: str,
    mode: str = "metadata_only",
    provider_route: str = NISAR_PROVIDER_ROUTE,
) -> dict[str, str]:
    settings = get_settings()
    engine = create_engine_if_needed(settings)
    service = create_nisar_ingestion_service(settings, engine)
    if getattr(self.request, "redelivered", False):
        service.recover_worker_lost(job_id)
    job = service.execute_backfill(job_id, mode=mode, provider_route=provider_route)
    return {"job_id": job.job_id, "status": job.status.value}
