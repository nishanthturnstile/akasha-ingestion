from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs.celery_app import celery_app
from akasha.jobs.sql_store import PostgresJobStore
from akasha.jobs.store import InMemoryJobStore, JobStatus
from akasha.providers.mock import MockProvider
from akasha.schemas import SyncRequest
from akasha.services.ingestion import MockIngestionService
from akasha.storage.object_store import InMemoryObjectStore


class NoMarkQueuedStore(InMemoryJobStore):
    def mark_queued(self, job):  # type: ignore[no-untyped-def]
        raise AssertionError("created jobs must not be rewritten after Celery dispatch")


def test_celery_mock_task_is_registered() -> None:
    assert "akasha.jobs.tasks.mock_sync" in celery_app.tasks
    assert "akasha.jobs.sentinel2_tasks.backfill" in celery_app.tasks


def test_failed_idempotent_job_can_be_retried() -> None:
    store = InMemoryJobStore()

    first, first_created = store.create_or_get(
        job_type="mock_sync",
        idempotency_key="same",
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start="2026-01-15",
        date_end="2026-04-15",
    )
    store.mark_failed(first, error="transient")
    second, second_created = store.create_or_get(
        job_type="mock_sync",
        idempotency_key="same",
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start="2026-01-15",
        date_end="2026-04-15",
    )

    assert first_created is True
    assert second_created is True
    assert first.job_id != second.job_id
    assert first.status == JobStatus.FAILED
    assert second.status == JobStatus.QUEUED


def test_completed_in_memory_job_clears_previous_error() -> None:
    store = InMemoryJobStore()
    job, _created = store.create_or_get(
        job_type="resourcesat_backfill",
        idempotency_key="retry-completes",
        source_id="resourcesat-2a-liss3-boa",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start="2026-01-13",
        date_end="2026-07-10",
    )
    store.mark_failed(job, error="transient failure")
    store.mark_running(job)

    completed = store.mark_completed(job, result_metadata={"processed_count": 1})

    assert completed.status == JobStatus.COMPLETED
    assert completed.error is None


def test_completed_postgres_job_clears_previous_error() -> None:
    engine = _CompletedJobEngine()
    store = PostgresJobStore(engine)  # type: ignore[arg-type]
    job, _created = InMemoryJobStore().create_or_get(
        job_type="resourcesat_backfill",
        idempotency_key="postgres-retry-completes",
        source_id="resourcesat-2a-liss3-boa",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start="2026-01-13",
        date_end="2026-07-10",
    )
    job.error = "transient failure"

    completed = store.mark_completed(job, result_metadata={"processed_count": 1})

    assert "error_message = NULL" in engine.statement
    assert completed.status == JobStatus.COMPLETED
    assert completed.error is None


def test_start_mock_sync_does_not_rewrite_status_after_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_tasks: list[tuple[str, list[str]]] = []

    def fake_send_task(name: str, args: list[str]) -> None:
        sent_tasks.append((name, args))

    monkeypatch.setattr(celery_app, "send_task", fake_send_task)
    service = MockIngestionService(
        job_store=NoMarkQueuedStore(),
        object_store=InMemoryObjectStore(),
        provider=MockProvider(),
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=False,
        ),
    )

    job = service.start_mock_sync(
        SyncRequest(
            source_id="sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
            date_start="2026-01-15",
            date_end="2026-04-15",
        )
    )

    assert job.status == JobStatus.QUEUED
    assert sent_tasks == [("akasha.jobs.tasks.mock_sync", [job.job_id])]


def test_dispatch_failure_marks_job_failed_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryJobStore()
    attempts = 0

    def fake_send_task(name: str, args: list[str]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(celery_app, "send_task", fake_send_task)
    service = MockIngestionService(
        job_store=store,
        object_store=InMemoryObjectStore(),
        provider=MockProvider(),
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=False,
        ),
    )
    request = SyncRequest(
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start="2026-01-15",
        date_end="2026-04-15",
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        service.start_mock_sync(request)
    retry = service.start_mock_sync(request)

    jobs = store.list()
    assert attempts == 2
    assert jobs[0].status == JobStatus.FAILED
    assert retry.status == JobStatus.QUEUED
    assert retry.job_id != jobs[0].job_id


class _CompletedJobEngine:
    def __init__(self) -> None:
        self.statement = ""

    def begin(self):
        return _CompletedJobConnection(self)


class _CompletedJobConnection:
    def __init__(self, engine: _CompletedJobEngine) -> None:
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, _params):
        self._engine.statement = str(statement)
        now = datetime.now(UTC)
        row = SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            job_type="resourcesat_backfill",
            idempotency_key="postgres-retry-completes",
            status="completed",
            source_id="resourcesat-2a-liss3-boa",
            aoi_id="bangalore_60km_geodesic_aoi",
            request_params={
                "date_start": "2026-01-13",
                "date_end": "2026-07-10",
            },
            result_metadata={"processed_count": 1},
            error_message=None,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        return _CompletedJobResult(row)


class _CompletedJobResult:
    def __init__(self, row) -> None:
        self._row = row

    def mappings(self):
        return self

    def one(self):
        return self._row
