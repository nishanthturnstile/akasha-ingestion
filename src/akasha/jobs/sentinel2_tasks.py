from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from akasha.config import Settings, get_settings
from akasha.jobs.celery_app import celery_app
from akasha.runtime import (
    create_aoi_repository,
    create_asset_repository,
    create_backfill_repository,
    create_engine_if_needed,
    create_job_store,
    create_object_store,
    create_pgstac_repository,
    create_raster_repository,
    create_scene_repository,
    create_stage_store,
    create_tile_layer_repository,
)
from akasha.schemas import SyncRequest
from akasha.services.sentinel2_ingestion import Sentinel2IngestionService


@celery_app.task(name="akasha.jobs.sentinel2_tasks.scheduled_bangalore_preload")
def scheduled_bangalore_preload() -> dict[str, str]:
    settings = get_settings()
    start_date, end_date = _scheduled_date_window(settings)
    service = _create_service()
    job = service.start_backfill(
        SyncRequest(
            source_id=settings.sentinel2_preload_source_id,
            provider_route=settings.sentinel2_preload_provider_route,
            aoi_id=settings.sentinel2_preload_aoi_id,
            date_start=start_date,
            date_end=end_date,
            job_type="sentinel2_backfill",
            mode=settings.sentinel2_preload_mode,
        )
    )
    return {"job_id": job.job_id, "status": job.status.value}


def _scheduled_date_window(
    settings: Settings,
    *,
    end_date: date | None = None,
) -> tuple[date, date]:
    """Return complete provider days for a periodic Sentinel-2 refresh."""
    resolved_end = end_date or datetime.now(UTC).date() - timedelta(days=1)
    lookback_days = settings.sentinel2_preload_refresh_days - 1
    return resolved_end - timedelta(days=lookback_days), resolved_end


@celery_app.task(bind=True, name="akasha.jobs.sentinel2_tasks.backfill")
def backfill(task, job_id: str, mode: str = "metadata_only") -> dict[str, str]:
    service = _create_service()
    delivery_info = getattr(task.request, "delivery_info", {}) or {}
    if delivery_info.get("redelivered"):
        service.recover_worker_lost(job_id)
    job = service.execute_backfill(job_id, mode=mode)
    return {"job_id": job.job_id, "status": job.status.value}


def _create_service() -> Sentinel2IngestionService:
    settings = get_settings()
    engine = create_engine_if_needed(settings)
    return Sentinel2IngestionService(
        job_store=create_job_store(settings, engine),
        stage_store=create_stage_store(settings, engine),
        aoi_repository=create_aoi_repository(settings, engine),
        scene_repository=create_scene_repository(settings, engine),
        asset_repository=create_asset_repository(settings, engine),
        raster_repository=create_raster_repository(settings, engine),
        object_store=create_object_store(settings),
        backfill_repository=create_backfill_repository(settings, engine),
        pgstac_repository=create_pgstac_repository(settings, engine),
        tile_layer_repository=create_tile_layer_repository(settings, engine),
        settings=settings,
    )
