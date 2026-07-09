from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from akasha.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    celery_app = Celery("akasha", broker=settings.redis_url, backend=settings.redis_url)
    celery_app.conf.task_default_queue = "maintenance"
    celery_app.conf.imports = (
        "akasha.jobs.tasks",
        "akasha.jobs.sentinel2_tasks",
        "akasha.jobs.resourcesat_tasks",
    )
    celery_app.conf.task_routes = {
        "akasha.jobs.tasks.mock_sync": {"queue": "download"},
        "akasha.jobs.sentinel2_tasks.scheduled_bangalore_preload": {"queue": "maintenance"},
        "akasha.jobs.sentinel2_tasks.backfill": {"queue": "search"},
        "akasha.jobs.resourcesat_tasks.scheduled_liss3_preload": {"queue": "maintenance"},
        "akasha.jobs.resourcesat_tasks.scheduled_resourcesat_sources": {"queue": "maintenance"},
        "akasha.jobs.resourcesat_tasks.backfill": {"queue": "heavy-cpu"},
        "akasha.jobs.resourcesat_tasks.provider_search": {"queue": "search"},
        "akasha.jobs.resourcesat_tasks.raw_download": {"queue": "download"},
        "akasha.jobs.resourcesat_tasks.prepare_scene": {"queue": "preprocess"},
        "akasha.jobs.resourcesat_tasks.composite": {"queue": "heavy-cpu"},
        "akasha.jobs.resourcesat_tasks.index_generation": {"queue": "cog"},
        "akasha.jobs.resourcesat_tasks.readiness_refresh": {"queue": "stats"},
    }
    beat_schedule = {}
    if settings.sentinel2_preload_schedule_enabled:
        beat_schedule["sentinel2-bangalore-preload-weekly"] = {
            "task": "akasha.jobs.sentinel2_tasks.scheduled_bangalore_preload",
            "schedule": crontab(
                minute=settings.sentinel2_preload_schedule_minute_utc,
                hour=settings.sentinel2_preload_schedule_hour_utc,
                day_of_week=settings.sentinel2_preload_schedule_day_of_week,
            ),
        }
    if (
        settings.resourcesat_liss3_preload_schedule_enabled
        or settings.resourcesat_liss4_preload_schedule_enabled
        or settings.resourcesat_awifs_preload_schedule_enabled
    ):
        beat_schedule["resourcesat-source-orchestration"] = {
            "task": "akasha.jobs.resourcesat_tasks.scheduled_resourcesat_sources",
            "schedule": crontab(minute=0, hour="*/6"),
        }
    if beat_schedule:
        celery_app.conf.beat_schedule = beat_schedule
    celery_app.conf.task_acks_late = True
    celery_app.conf.worker_prefetch_multiplier = 1
    return celery_app


celery_app = create_celery_app()

import akasha.jobs.resourcesat_tasks  # noqa: E402,F401
import akasha.jobs.sentinel2_tasks  # noqa: E402,F401
import akasha.jobs.tasks  # noqa: E402,F401
