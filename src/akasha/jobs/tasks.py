from __future__ import annotations

from akasha.config import get_settings
from akasha.jobs.celery_app import celery_app
from akasha.providers.mock import MockProvider
from akasha.runtime import create_engine_if_needed, create_job_store, create_object_store
from akasha.services.ingestion import MockIngestionService


@celery_app.task(name="akasha.jobs.tasks.mock_sync")
def mock_sync(job_id: str) -> dict[str, str]:
    settings = get_settings()
    engine = create_engine_if_needed(settings)
    service = MockIngestionService(
        job_store=create_job_store(settings, engine),
        object_store=create_object_store(settings),
        provider=MockProvider(),
        settings=settings,
    )
    job = service.execute_mock_sync(job_id)
    return {"job_id": job.job_id, "status": job.status.value}
