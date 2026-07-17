from __future__ import annotations

from datetime import UTC, datetime

from akasha.config import RuntimeBackend, get_settings
from akasha.jobs.celery_app import celery_app
from akasha.jobs.store import JobStatus
from akasha.runtime import (
    create_engine_if_needed,
    create_job_store,
    create_resourcesat_ingestion_service,
)
from akasha.scheduler.locks import InMemorySourceAoiLockRegistry, PostgresSourceAoiLockRegistry
from akasha.scheduler.orchestrator import run_source_job
from akasha.scheduler.planner import plan_due_sources


@celery_app.task(name="akasha.jobs.resourcesat_tasks.scheduled_liss3_preload")
def scheduled_liss3_preload() -> dict[str, object]:
    settings = get_settings()
    results = _run_scheduled_sources(
        source_filter={settings.resourcesat_liss3_preload_source_id},
        dry_run=False,
    )
    return results[0] if results else {"status": "blocked", "reason": "no LISS-3 plan found"}


@celery_app.task(name="akasha.jobs.resourcesat_tasks.scheduled_resourcesat_sources")
def scheduled_resourcesat_sources(dry_run: bool = False) -> list[dict[str, object]]:
    return _run_scheduled_sources(source_filter=None, dry_run=dry_run)


@celery_app.task(bind=True, name="akasha.jobs.resourcesat_tasks.backfill")
def backfill(
    task,
    job_id: str,
    mode: str = "metadata_only",
    provider_route: str | None = None,
) -> dict[str, str]:
    service = _create_service()
    delivery_info = getattr(task.request, "delivery_info", {}) or {}
    if delivery_info.get("redelivered"):
        service.recover_worker_lost(job_id)
    job = service.execute_backfill(job_id, mode=mode, provider_route=provider_route)
    return {"job_id": job.job_id, "status": job.status.value}


@celery_app.task(name="akasha.jobs.resourcesat_tasks.provider_search")
def provider_search(job_id: str, provider_route: str | None = None) -> dict[str, str]:
    return _execute_backfill(job_id, mode="metadata_only", provider_route=provider_route)


@celery_app.task(name="akasha.jobs.resourcesat_tasks.raw_download")
def raw_download(job_id: str, provider_route: str | None = None) -> dict[str, str]:
    return _execute_backfill(job_id, mode="download_only", provider_route=provider_route)


@celery_app.task(name="akasha.jobs.resourcesat_tasks.prepare_scene")
def prepare_scene(job_id: str, provider_route: str | None = None) -> dict[str, str]:
    return _execute_backfill(job_id, mode="prepare_only", provider_route=provider_route)


@celery_app.task(name="akasha.jobs.resourcesat_tasks.composite")
def composite(job_id: str, provider_route: str | None = None) -> dict[str, str]:
    return _execute_backfill(job_id, mode="composite_only", provider_route=provider_route)


@celery_app.task(name="akasha.jobs.resourcesat_tasks.index_generation")
def index_generation(job_id: str, provider_route: str | None = None) -> dict[str, str]:
    return _execute_backfill(job_id, mode="full_pipeline", provider_route=provider_route)


@celery_app.task(name="akasha.jobs.resourcesat_tasks.readiness_refresh")
def readiness_refresh(job_id: str, provider_route: str | None = None) -> dict[str, str]:
    return _execute_backfill(job_id, mode="full_pipeline", provider_route=provider_route)


def _execute_backfill(
    job_id: str,
    *,
    mode: str,
    provider_route: str | None,
) -> dict[str, str]:
    service = _create_service()
    job = service.execute_backfill(job_id, mode=mode, provider_route=provider_route)
    return {"job_id": job.job_id, "status": job.status.value}


def _create_service():
    settings = get_settings()
    engine = create_engine_if_needed(settings)
    return create_resourcesat_ingestion_service(settings, engine)


def _run_scheduled_sources(
    *,
    source_filter: set[str] | None,
    dry_run: bool,
) -> list[dict[str, object]]:
    settings = get_settings()
    engine = create_engine_if_needed(settings)
    job_store = create_job_store(settings, engine)
    service = create_resourcesat_ingestion_service(settings, engine, job_store=job_store)
    lock_registry = (
        InMemorySourceAoiLockRegistry()
        if settings.runtime_backend == RuntimeBackend.MEMORY or engine is None
        else PostgresSourceAoiLockRegistry(engine)
    )
    plans = plan_due_sources(
        settings=settings,
        now=datetime.now(UTC),
        last_success_by_source_aoi=_last_success_by_source_aoi(job_store),
        dry_run=dry_run,
    )
    if source_filter is not None:
        plans = [plan for plan in plans if plan.source_id in source_filter]
    results = [
        run_source_job(
            plan=plan,
            settings=settings,
            resourcesat_service=service,
            lock_registry=lock_registry,
        )
        for plan in plans
    ]
    return [result.metadata() for result in results]


def _last_success_by_source_aoi(job_store) -> dict[tuple[str, str], datetime]:
    last_success: dict[tuple[str, str], datetime] = {}
    for job in job_store.list():
        if job.job_type != "resourcesat_backfill" or job.status != JobStatus.COMPLETED:
            continue
        if job.result_metadata.get("mode") != "full_pipeline":
            continue
        completed_at = job.completed_at or job.updated_at
        key = (job.source_id, job.aoi_id)
        if key not in last_success or completed_at > last_success[key]:
            last_success[key] = completed_at
    return last_success
