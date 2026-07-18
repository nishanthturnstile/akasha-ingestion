from __future__ import annotations

from datetime import UTC, datetime, timedelta

from akasha.config import get_settings
from akasha.jobs.celery_app import celery_app
from akasha.runtime import create_engine_if_needed, create_eos04_ingestion_service
from akasha.schemas import SyncRequest


@celery_app.task(name="akasha.jobs.eos04_tasks.scheduled_preload")
def scheduled_preload() -> dict[str, str]:
    settings = get_settings()
    today = datetime.now(UTC).date()
    service = create_eos04_ingestion_service(
        settings,
        create_engine_if_needed(settings),
    )
    job = service.start_backfill(
        SyncRequest(
            source_id=settings.eos04_preload_source_id,
            provider_route=settings.eos04_preload_provider_route,
            aoi_id=settings.eos04_preload_aoi_id,
            date_start=today - timedelta(days=settings.eos04_preload_date_window_days),
            date_end=today,
            job_type="eos04_backfill",
            mode="full_pipeline",
        )
    )
    return {"job_id": job.job_id, "status": job.status.value}


@celery_app.task(bind=True, name="akasha.jobs.eos04_tasks.backfill")
def backfill(
    task,
    job_id: str,
    mode: str = "metadata_only",
    provider_route: str | None = None,
) -> dict[str, str]:
    settings = get_settings()
    service = create_eos04_ingestion_service(
        settings,
        create_engine_if_needed(settings),
    )
    delivery_info = getattr(task.request, "delivery_info", {}) or {}
    if delivery_info.get("redelivered"):
        service.recover_worker_lost(job_id)
    job = service.execute_backfill(
        job_id,
        mode=mode,
        provider_route=provider_route or settings.eos04_preload_provider_route,
    )
    return {"job_id": job.job_id, "status": job.status.value}
