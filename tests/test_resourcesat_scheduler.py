from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from akasha.config import Environment, RuntimeBackend, Settings
from akasha.scheduler.locks import InMemorySourceAoiLockRegistry, source_aoi_lock_name
from akasha.scheduler.orchestrator import run_source_job
from akasha.scheduler.planner import plan_due_sources

SOURCE_ID = "resourcesat-2a-liss3-boa"
AOI_ID = "bangalore_60km_geodesic_aoi"
PROVIDER_ROUTE = "bhoonidhi:ResourceSat-2A_LISS3_BOA"


def test_plan_due_sources_is_deterministic_and_makes_no_provider_calls() -> None:
    settings = _settings(resourcesat_liss3_preload_schedule_enabled=True)
    now = datetime(2026, 7, 9, 6, 0, tzinfo=UTC)

    plans = plan_due_sources(settings=settings, now=now)

    liss3 = next(plan for plan in plans if plan.source_id == SOURCE_ID)
    assert liss3.decision == "due"
    assert liss3.date_start.isoformat() == "2026-06-09"
    assert liss3.date_end.isoformat() == "2026-07-09"
    assert liss3.thresholds == {
        "maxDownloads": 1,
        "minCoveragePercent": 95.0,
        "compositeWindowDays": 30,
        "freshnessMaxAgeHours": 336,
    }


def test_plan_due_sources_skips_recent_success() -> None:
    settings = _settings(resourcesat_liss3_preload_schedule_enabled=True)
    now = datetime(2026, 7, 9, 6, 0, tzinfo=UTC)
    last_success = {(SOURCE_ID, AOI_ID): now - timedelta(days=1)}

    liss3 = next(
        plan
        for plan in plan_due_sources(
            settings=settings,
            now=now,
            last_success_by_source_aoi=last_success,
        )
        if plan.source_id == SOURCE_ID
    )

    assert liss3.decision == "not_due"
    assert "next run is due" in liss3.reason


def test_scheduler_dry_run_does_not_call_provider_or_mutate(caplog) -> None:
    settings = _settings()
    plan = _manual_plan(settings, dry_run=True)
    service = _FakeResourceSatService()

    with caplog.at_level(logging.INFO, logger="akasha.scheduler.orchestrator"):
        result = run_source_job(
            plan=plan,
            settings=settings,
            resourcesat_service=service,
            lock_registry=InMemorySourceAoiLockRegistry(),
        )

    assert result.status == "planned"
    assert result.counts == {"plannedStages": 10, "providerCalls": 0, "mutations": 0}
    assert result.metadata()["sourceId"] == SOURCE_ID
    assert service.calls == []
    assert caplog.records[-1].scheduler_run["status"] == "planned"
    assert "raw/" not in str(caplog.records[-1].scheduler_run)


def test_scheduler_live_manual_source_is_blocked_by_default() -> None:
    settings = _settings()
    plan = _manual_plan(settings, dry_run=False)

    result = run_source_job(
        plan=plan,
        settings=settings,
        resourcesat_service=_FakeResourceSatService(),
        lock_registry=InMemorySourceAoiLockRegistry(),
    )

    assert result.status == "blocked"
    assert result.error_category == "source_gated"
    assert "manual" in result.reason


def test_scheduler_live_job_requires_approved_runtime() -> None:
    settings = _settings(
        resourcesat_liss3_preload_schedule_enabled=True,
        bhoonidhi_approved_runtime_required=True,
        bhoonidhi_approved_runtime=False,
    )
    plan = _scheduled_plan(settings)

    result = run_source_job(
        plan=plan,
        settings=settings,
        resourcesat_service=_FakeResourceSatService(),
        lock_registry=InMemorySourceAoiLockRegistry(),
    )

    assert result.status == "failed"
    assert result.error_category == "approved_runtime_required"
    assert "approved runtime" in result.reason


def test_scheduler_disk_headroom_failure_is_classified(monkeypatch) -> None:
    settings = _settings(
        resourcesat_liss3_preload_schedule_enabled=True,
        bhoonidhi_approved_runtime=True,
        source_mirror_required_headroom_bytes=10_000,
    )
    plan = _scheduled_plan(settings)
    monkeypatch.setattr(
        "akasha.scheduler.orchestrator.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1),
    )

    result = run_source_job(
        plan=plan,
        settings=settings,
        resourcesat_service=_FakeResourceSatService(),
        lock_registry=InMemorySourceAoiLockRegistry(),
    )

    assert result.status == "failed"
    assert result.error_category == "insufficient_headroom"
    assert "headroom" in result.reason


def test_scheduler_runtime_root_failure_metadata_does_not_leak_paths(caplog) -> None:
    settings = _settings(
        runtime_backend=RuntimeBackend.EXTERNAL,
        resourcesat_liss3_preload_schedule_enabled=True,
        bhoonidhi_approved_runtime=True,
        scratch_dir="/tmp/akasha",
        resourcesat_approved_data_root="/srv/akasha",
    )
    plan = _scheduled_plan(settings)

    with caplog.at_level(logging.INFO, logger="akasha.scheduler.orchestrator"):
        result = run_source_job(
            plan=plan,
            settings=settings,
            resourcesat_service=_FakeResourceSatService(),
            lock_registry=InMemorySourceAoiLockRegistry(),
        )

    assert result.status == "failed"
    assert result.reason == "ResourceSat runtime root failed approved-root validation"
    assert "/tmp/akasha" not in str(caplog.records[-1].scheduler_run)


def test_scheduler_lock_blocks_duplicate_source_aoi_run() -> None:
    settings = _settings(resourcesat_liss3_preload_schedule_enabled=True)
    registry = InMemorySourceAoiLockRegistry()
    plan = _scheduled_plan(settings)

    with registry.acquire(source_id=SOURCE_ID, aoi_id=AOI_ID) as acquired:
        assert acquired is True
        result = run_source_job(
            plan=plan,
            settings=settings,
            resourcesat_service=_FakeResourceSatService(),
            lock_registry=registry,
        )

    assert result.status == "blocked"
    assert result.error_category == "lock_held"
    assert source_aoi_lock_name(source_id=SOURCE_ID, aoi_id=AOI_ID) in result.reason


def test_scheduler_success_dispatches_resourcesat_job() -> None:
    settings = _settings(
        resourcesat_liss3_preload_schedule_enabled=True,
        bhoonidhi_approved_runtime=True,
    )
    service = _FakeResourceSatService()
    plan = _scheduled_plan(settings)

    result = run_source_job(
        plan=plan,
        settings=settings,
        resourcesat_service=service,
        lock_registry=InMemorySourceAoiLockRegistry(),
    )

    assert result.status == "dispatched"
    assert result.job_id == "job-scheduler-1"
    assert service.calls == [(SOURCE_ID, AOI_ID, PROVIDER_ROUTE, "full_pipeline")]


def _settings(**overrides: object) -> Settings:
    values = {
        "environment": Environment.TEST,
        "runtime_backend": RuntimeBackend.MEMORY,
        "scratch_dir": "C:\\akasha-test\\scratch",
    }
    values.update(overrides)
    return Settings(**values)


def _manual_plan(settings: Settings, *, dry_run: bool):
    return next(
        plan
        for plan in plan_due_sources(
            settings=settings,
            now=datetime(2026, 7, 9, 6, 0, tzinfo=UTC),
            include_manual=True,
            dry_run=dry_run,
        )
        if plan.source_id == SOURCE_ID
    )


def _scheduled_plan(settings: Settings):
    return next(
        plan
        for plan in plan_due_sources(
            settings=settings,
            now=datetime(2026, 7, 9, 6, 0, tzinfo=UTC),
        )
        if plan.source_id == SOURCE_ID
    )


class _FakeResourceSatService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def start_backfill(self, request):
        self.calls.append(
            (
                request.source_id,
                request.aoi_id,
                request.provider_route,
                request.mode,
            )
        )
        return SimpleNamespace(
            job_id="job-scheduler-1",
            status=SimpleNamespace(value="queued"),
        )
