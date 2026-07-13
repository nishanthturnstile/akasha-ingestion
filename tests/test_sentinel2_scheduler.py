from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs import sentinel2_tasks


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


def test_scheduled_preload_submits_bounded_full_pipeline_request(monkeypatch) -> None:
    captured = []

    class Service:
        def start_backfill(self, request):
            captured.append(request)
            return SimpleNamespace(job_id="job-1", status=SimpleNamespace(value="queued"))

    monkeypatch.setattr(sentinel2_tasks, "get_settings", _settings)
    monkeypatch.setattr(
        sentinel2_tasks,
        "_scheduled_date_window",
        lambda _settings: (date(2026, 7, 7), date(2026, 7, 13)),
    )
    monkeypatch.setattr(sentinel2_tasks, "_create_service", Service)

    result = sentinel2_tasks.scheduled_bangalore_preload()

    assert result == {"job_id": "job-1", "status": "queued"}
    assert len(captured) == 1
    request = captured[0]
    assert request.date_start == date(2026, 7, 7)
    assert request.date_end == date(2026, 7, 13)
    assert request.mode == "full_pipeline"


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
