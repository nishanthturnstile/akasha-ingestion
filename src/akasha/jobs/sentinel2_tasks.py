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
    create_sync_ledger_repository,
    create_tile_layer_repository,
)
from akasha.schemas import SyncRequest
from akasha.services.sentinel2_ingestion import Sentinel2IngestionService


@celery_app.task(name="akasha.jobs.sentinel2_tasks.scheduled_bangalore_preload")
def scheduled_bangalore_preload() -> dict[str, str]:
    settings = get_settings()
    service = _create_service()
    if service.has_active_backfill(
        source_id=settings.sentinel2_preload_source_id,
        aoi_id=settings.sentinel2_preload_aoi_id,
    ):
        return {"job_id": "", "status": "active"}
    date_window = _scheduled_date_window(
        settings,
        ledger_records=(
            service._sync_ledger_repository.list_for_source_aoi(
                source_id=settings.sentinel2_preload_source_id,
                aoi_id=settings.sentinel2_preload_aoi_id,
            )
            if getattr(service, "_sync_ledger_repository", None) is not None
            else None
        ),
    )
    if date_window is None:
        return {"job_id": "", "status": "not_due"}
    start_date, end_date = date_window
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
    latest_processed_date: date | None = None,
    ledger_records: list[object] | None = None,
) -> tuple[date, date] | None:
    """Return a daily seed/overlap range based on the sync ledger, never a scene cursor."""
    resolved_end = end_date or datetime.now(UTC).date() - timedelta(days=1)
    del latest_processed_date  # retained as a compatibility argument; it is not a cursor.
    records = ledger_records or []
    if not records:
        return (
            resolved_end - timedelta(days=settings.sentinel2_preload_date_window_days - 1),
            resolved_end,
        )
    seed_start = resolved_end - timedelta(days=settings.sentinel2_preload_date_window_days - 1)
    known_dates = {record.provider_date for record in records}
    missing_dates = [
        seed_start + timedelta(days=offset)
        for offset in range(settings.sentinel2_preload_date_window_days)
        if seed_start + timedelta(days=offset) not in known_dates
    ]
    overlap_start = resolved_end - timedelta(days=settings.sentinel2_preload_refresh_days - 1)
    incomplete_dates = [
        record.provider_date
        for record in records
        if getattr(record, "status", None) != "complete"
        and record.provider_date <= resolved_end
    ]
    start_date = min([overlap_start, *missing_dates, *incomplete_dates])
    return start_date, resolved_end


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
        sync_ledger_repository=create_sync_ledger_repository(settings, engine),
        settings=settings,
    )
