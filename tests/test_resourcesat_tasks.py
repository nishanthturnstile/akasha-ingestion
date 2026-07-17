from __future__ import annotations

from types import SimpleNamespace

from akasha.jobs import resourcesat_tasks
from akasha.jobs.celery_app import celery_app
from akasha.services.resourcesat_ingestion import ResourceSatIngestionService


def test_resourcesat_celery_routes_use_existing_queues() -> None:
    routes = celery_app.conf.task_routes

    assert routes["akasha.jobs.resourcesat_tasks.scheduled_liss3_preload"]["queue"] == "maintenance"
    assert (
        routes["akasha.jobs.resourcesat_tasks.scheduled_resourcesat_sources"]["queue"]
        == "maintenance"
    )
    assert routes["akasha.jobs.resourcesat_tasks.backfill"]["queue"] == "heavy-cpu"
    assert routes["akasha.jobs.resourcesat_tasks.provider_search"]["queue"] == "search"
    assert routes["akasha.jobs.resourcesat_tasks.raw_download"]["queue"] == "download"
    assert routes["akasha.jobs.resourcesat_tasks.prepare_scene"]["queue"] == "preprocess"
    assert routes["akasha.jobs.resourcesat_tasks.composite"]["queue"] == "heavy-cpu"
    assert routes["akasha.jobs.resourcesat_tasks.index_generation"]["queue"] == "cog"
    assert routes["akasha.jobs.resourcesat_tasks.readiness_refresh"]["queue"] == "stats"


def test_resourcesat_backfill_task_invokes_service(monkeypatch) -> None:
    fake_service = _FakeService()
    monkeypatch.setattr(resourcesat_tasks, "_create_service", lambda: fake_service)

    result = resourcesat_tasks.backfill.run(
        "job-1",
        mode="download_only",
        provider_route="bhoonidhi:ResourceSat-2A_LISS3_BOA",
    )

    assert result == {"job_id": "job-1", "status": "completed"}
    assert fake_service.calls == [
        ("job-1", "download_only", "bhoonidhi:ResourceSat-2A_LISS3_BOA")
    ]


def test_resourcesat_scheduler_task_invokes_orchestration(monkeypatch) -> None:
    monkeypatch.setattr(
        resourcesat_tasks,
        "_run_scheduled_sources",
        lambda *, source_filter, dry_run: [
            {"status": "planned", "dryRun": dry_run, "sourceFilter": sorted(source_filter or [])}
        ],
    )

    result = resourcesat_tasks.scheduled_resourcesat_sources.run(dry_run=True)

    assert result == [{"status": "planned", "dryRun": True, "sourceFilter": []}]


def test_redelivered_resourcesat_backfill_recovers_worker_lost_state(monkeypatch) -> None:
    fake_service = _FakeService()
    monkeypatch.setattr(resourcesat_tasks, "_create_service", lambda: fake_service)
    resourcesat_tasks.backfill.push_request(delivery_info={"redelivered": True})
    try:
        result = resourcesat_tasks.backfill.run("job-1", mode="full_pipeline")
    finally:
        resourcesat_tasks.backfill.pop_request()

    assert result == {"job_id": "job-1", "status": "completed"}
    assert fake_service.recovered == ["job-1"]


def test_resourcesat_recovery_closes_running_stages_and_requeues_job() -> None:
    running = SimpleNamespace(stage_id="stage-running", status=SimpleNamespace(value="running"))
    completed = SimpleNamespace(
        stage_id="stage-completed",
        status=SimpleNamespace(value="completed"),
    )
    job = SimpleNamespace(job_id="job-1")
    job_store = SimpleNamespace(
        get=lambda job_id: job,
        mark_queued=lambda value: queued.append(value),
    )
    stage_store = SimpleNamespace(
        list_for_job=lambda job_id: [running, completed],
        mark_failed=lambda stage_id, **kwargs: failed.append((stage_id, kwargs)),
    )
    queued = []
    failed = []
    service = object.__new__(ResourceSatIngestionService)
    service._job_store = job_store
    service._stage_store = stage_store

    service.recover_worker_lost("job-1")

    assert queued == [job]
    assert failed == [
        (
            "stage-running",
            {
                "error_code": "worker_lost",
                "error_message": (
                    "Celery redelivered the task after its worker process exited."
                ),
            },
        )
    ]


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.recovered: list[str] = []

    def recover_worker_lost(self, job_id: str) -> None:
        self.recovered.append(job_id)

    def execute_backfill(
        self,
        job_id: str,
        *,
        mode: str,
        provider_route: str | None,
    ):
        self.calls.append((job_id, mode, provider_route))
        return SimpleNamespace(
            job_id=job_id,
            status=SimpleNamespace(value="completed"),
        )
