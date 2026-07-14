from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from akasha.catalog.aoi_repository import AoiRecord
from akasha.catalog.backfill_repository import InMemoryBackfillRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs import celery_app as celery_app_module
from akasha.jobs import sentinel2_tasks
from akasha.jobs.stage_store import InMemoryStageStore
from akasha.jobs.store import InMemoryJobStore
from akasha.schemas import SyncRequest
from akasha.services.sentinel2_ingestion import Sentinel2IngestionService


def _settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        sentinel2_preload_date_window_days=180,
        sentinel2_preload_refresh_days=7,
    )


def test_scheduled_date_window_uses_refresh_window_not_bootstrap_window() -> None:
    start_date, end_date = sentinel2_tasks._scheduled_date_window(
        _settings(),
        end_date=date(2026, 7, 13),
    )

    assert start_date == date(2026, 7, 7)
    assert end_date == date(2026, 7, 13)


def test_scheduled_date_window_excludes_incomplete_current_day(monkeypatch) -> None:
    class FixedDateTime:
        @staticmethod
        def now(_timezone):
            return SimpleNamespace(date=lambda: date(2026, 7, 13))

    monkeypatch.setattr(sentinel2_tasks, "datetime", FixedDateTime)

    start_date, end_date = sentinel2_tasks._scheduled_date_window(_settings())

    assert start_date == date(2026, 7, 6)
    assert end_date == date(2026, 7, 12)


def test_consecutive_daily_checks_shift_the_bounded_window() -> None:
    first = sentinel2_tasks._scheduled_date_window(
        _settings(),
        end_date=date(2026, 7, 13),
    )
    second = sentinel2_tasks._scheduled_date_window(
        _settings(),
        end_date=date(2026, 7, 14),
    )

    assert first == (date(2026, 7, 7), date(2026, 7, 13))
    assert second == (date(2026, 7, 8), date(2026, 7, 14))


def test_outstanding_expected_pass_remains_in_window_until_data_arrives() -> None:
    first = sentinel2_tasks._scheduled_date_window(
        _settings(),
        end_date=date(2026, 7, 13),
        latest_processed_date=date(2026, 6, 28),
    )
    later = sentinel2_tasks._scheduled_date_window(
        _settings(),
        end_date=date(2026, 7, 20),
        latest_processed_date=date(2026, 6, 28),
    )

    assert first == (date(2026, 7, 3), date(2026, 7, 13))
    assert later == (date(2026, 7, 3), date(2026, 7, 20))


def test_daily_check_is_not_due_before_next_expected_pass_is_complete() -> None:
    assert sentinel2_tasks._scheduled_date_window(
        _settings(),
        end_date=date(2026, 7, 2),
        latest_processed_date=date(2026, 6, 28),
    ) is None


def test_scheduled_preload_submits_bounded_full_pipeline_request(monkeypatch) -> None:
    captured = []

    class Service:
        def has_active_backfill(self, **_kwargs):
            return False

        def latest_processed_acquisition_date(self, **_kwargs):
            return None

        def start_backfill(self, request):
            captured.append(request)
            return SimpleNamespace(job_id="job-1", status=SimpleNamespace(value="queued"))

    monkeypatch.setattr(sentinel2_tasks, "get_settings", _settings)
    monkeypatch.setattr(
        sentinel2_tasks,
        "_scheduled_date_window",
        lambda _settings, **_kwargs: (date(2026, 7, 7), date(2026, 7, 13)),
    )
    monkeypatch.setattr(sentinel2_tasks, "_create_service", Service)

    result = sentinel2_tasks.scheduled_bangalore_preload()

    assert result == {"job_id": "job-1", "status": "queued"}
    assert len(captured) == 1
    request = captured[0]
    assert request.date_start == date(2026, 7, 7)
    assert request.date_end == date(2026, 7, 13)
    assert request.mode == "full_pipeline"


def test_scheduled_preload_does_not_queue_behind_active_job(monkeypatch) -> None:
    class Service:
        def has_active_backfill(self, **_kwargs):
            return True

        def latest_processed_acquisition_date(self, **_kwargs):
            raise AssertionError("active jobs must short-circuit date planning")

        def start_backfill(self, _request):
            raise AssertionError("active jobs must not dispatch another backfill")

    monkeypatch.setattr(sentinel2_tasks, "get_settings", _settings)
    monkeypatch.setattr(sentinel2_tasks, "_create_service", Service)

    assert sentinel2_tasks.scheduled_bangalore_preload() == {
        "job_id": "",
        "status": "active",
    }


def test_celery_beat_registers_daily_sentinel_discovery(monkeypatch) -> None:
    settings = _settings()
    settings.sentinel2_preload_schedule_enabled = True
    settings.sentinel2_preload_schedule_hour_utc = 2
    settings.sentinel2_preload_schedule_minute_utc = 30
    settings.sentinel2_preload_schedule_day_of_week = "mon"
    monkeypatch.setattr(celery_app_module, "get_settings", lambda: settings)

    app = celery_app_module.create_celery_app()

    assert "sentinel2-bangalore-preload-weekly" not in app.conf.beat_schedule
    entry = app.conf.beat_schedule["sentinel2-bangalore-preload-daily"]
    assert entry["task"] == "akasha.jobs.sentinel2_tasks.scheduled_bangalore_preload"
    schedule = entry["schedule"]
    assert schedule.minute == {30}
    assert schedule.hour == {2}
    assert schedule.day_of_week == set(range(7))


def test_no_result_daily_checks_retry_shifted_window_with_cloud_cap() -> None:
    settings = _settings()
    settings.task_always_eager = True
    provider = _NoResultProvider()
    jobs = InMemoryJobStore()
    service = Sentinel2IngestionService(
        job_store=jobs,
        stage_store=InMemoryStageStore(),
        backfill_repository=InMemoryBackfillRepository(),
        settings=settings,
        aoi_repository=_AoiRepository(),
        scene_repository=object(),
        asset_repository=object(),
        raster_repository=object(),
        object_store=object(),
        tile_layer_repository=object(),
        provider=provider,
    )

    first = service.start_backfill(_sync_request(date(2026, 7, 7), date(2026, 7, 13)))
    second = service.start_backfill(_sync_request(date(2026, 7, 8), date(2026, 7, 14)))

    assert first.job_id != second.job_id
    assert first.result_metadata["backfill_summary"]["searched_count"] == 0
    assert second.result_metadata["backfill_summary"]["searched_count"] == 0
    assert [(request.date_start, request.date_end) for request in provider.requests] == [
        (date(2026, 7, 7), date(2026, 7, 13)),
        (date(2026, 7, 8), date(2026, 7, 14)),
    ]
    assert all(request.max_cloud_percentage == 20 for request in provider.requests)


def test_redelivered_backfill_recovers_worker_lost_state(monkeypatch) -> None:
    calls = []

    class Service:
        def recover_worker_lost(self, job_id):
            calls.append(("recover", job_id))

        def execute_backfill(self, job_id, *, mode):
            calls.append(("execute", job_id, mode))
            return SimpleNamespace(job_id=job_id, status=SimpleNamespace(value="completed"))

    monkeypatch.setattr(sentinel2_tasks, "_create_service", Service)
    sentinel2_tasks.backfill.push_request(delivery_info={"redelivered": True})
    try:
        result = sentinel2_tasks.backfill.run("job-1", mode="full_pipeline")
    finally:
        sentinel2_tasks.backfill.pop_request()

    assert result == {"job_id": "job-1", "status": "completed"}
    assert calls == [
        ("recover", "job-1"),
        ("execute", "job-1", "full_pipeline"),
    ]


class _AoiRepository:
    def get(self, aoi_id: str) -> AoiRecord:
        return AoiRecord(
            aoi_id=aoi_id,
            name="Bangalore",
            geometry={
                "type": "Polygon",
                "coordinates": [[[77, 12], [78, 12], [78, 13], [77, 12]]],
            },
            bbox=[77, 12, 78, 13],
        )


class _NoResultProvider:
    def __init__(self) -> None:
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        return []


def _sync_request(date_start: date, date_end: date) -> SyncRequest:
    return SyncRequest(
        source_id="sentinel-2-l2a",
        provider_route="earthsearch:sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start=date_start,
        date_end=date_end,
        job_type="sentinel2_backfill",
        mode="full_pipeline",
    )
