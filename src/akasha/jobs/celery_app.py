from __future__ import annotations

from celery import Celery

from akasha.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    celery_app = Celery("akasha", broker=settings.redis_url, backend=settings.redis_url)
    celery_app.conf.task_default_queue = "maintenance"
    celery_app.conf.imports = ("akasha.jobs.tasks", "akasha.jobs.sentinel2_tasks")
    celery_app.conf.task_routes = {
        "akasha.jobs.tasks.mock_sync": {"queue": "download"},
        "akasha.jobs.sentinel2_tasks.backfill": {"queue": "search"},
    }
    celery_app.conf.task_acks_late = True
    celery_app.conf.worker_prefetch_multiplier = 1
    return celery_app


celery_app = create_celery_app()

import akasha.jobs.sentinel2_tasks  # noqa: E402,F401
import akasha.jobs.tasks  # noqa: E402,F401
