from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

from akasha.config import Settings
from akasha.db.session import create_db_engine
from akasha.jobs.store import JobStatus
from akasha.processing.resourcesat import (
    INGESTION_TO_PRODUCT_AOI,
    profile_for_source,
)
from akasha.providers.bhoonidhi import BhoonidhiClient
from akasha.runtime import (
    create_aoi_repository,
    create_job_store,
    create_resourcesat_ingestion_service,
)
from akasha.scheduler.locks import PostgresSourceAoiLockRegistry
from akasha.scheduler.orchestrator import run_source_job
from akasha.scheduler.planner import PlannedSourceRun, plan_due_sources
from akasha.scheduler.source_registry import source_state_by_id

PRODUCT_TO_INGESTION_AOI = {value: key for key, value in INGESTION_TO_PRODUCT_AOI.items()}
TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED}


def _canonical_aoi(value: str) -> str:
    return PRODUCT_TO_INGESTION_AOI.get(value, value)


def _json(value: object) -> None:
    print(json.dumps(value, default=str, sort_keys=True))


def _settings_for_source(args: argparse.Namespace) -> Settings:
    settings = Settings()
    updates: dict[str, Any] = {}
    source = args.source
    if getattr(args, "approved_runtime", False):
        updates["bhoonidhi_approved_runtime"] = True
    if getattr(args, "max_downloads", None) is not None:
        field = {
            "resourcesat-2a-liss3-boa": "resourcesat_liss3_max_downloads_per_run",
            "resourcesat-2a-liss4-mx70-l2": "resourcesat_liss4_max_downloads_per_run",
            "resourcesat-2a-awifs-boa": "resourcesat_awifs_max_downloads_per_run",
        }[source]
        updates[field] = args.max_downloads
    if getattr(args, "min_coverage_percent", None) is not None:
        field = {
            "resourcesat-2a-liss3-boa": "resourcesat_liss3_composite_min_coverage_percent",
            "resourcesat-2a-liss4-mx70-l2": "resourcesat_liss4_composite_min_coverage_percent",
            "resourcesat-2a-awifs-boa": "resourcesat_awifs_composite_min_coverage_percent",
        }[source]
        updates[field] = args.min_coverage_percent
    return settings.model_copy(update=updates)


def _manual_plan(args: argparse.Namespace, settings: Settings) -> PlannedSourceRun:
    source = source_state_by_id(settings, args.source)
    if source is None:
        raise ValueError(f"unsupported source: {args.source}")
    aoi = _canonical_aoi(args.aoi)
    configured = next((item for item in source.default_aois if item.aoi_id == aoi), None)
    if configured is None:
        raise ValueError(f"AOI {args.aoi} is not configured for {args.source}")
    return PlannedSourceRun(
        source_id=args.source,
        aoi_id=aoi,
        provider_route=configured.provider_route,
        date_start=date.fromisoformat(args.window_start),
        date_end=date.fromisoformat(args.window_end),
        decision="due",
        reason="bounded manual run",
        dry_run=args.dry_run,
        thresholds={
            "maxDownloads": configured.max_downloads,
            "minCoveragePercent": configured.min_coverage_percent,
            "compositeWindowDays": (
                date.fromisoformat(args.window_end) - date.fromisoformat(args.window_start)
            ).days
            + 1,
            "freshnessMaxAgeHours": configured.freshness_max_age_hours,
        },
    )


def _wait_for_job(job_store: object, job_id: str, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while True:
        job = job_store.get(job_id)
        if job is None:
            raise RuntimeError(f"dispatched job disappeared: {job_id}")
        if job.status in TERMINAL_JOB_STATUSES:
            payload = {
                "jobId": job.job_id,
                "status": job.status.value,
                "sourceId": job.source_id,
                "aoiId": job.aoi_id,
                "dateStart": job.date_start,
                "dateEnd": job.date_end,
                "result": job.result_metadata,
                "error": job.error,
            }
            if job.status == JobStatus.FAILED:
                _json(payload)
                raise RuntimeError(job.error or f"job failed: {job_id}")
            return payload
        if time.monotonic() >= deadline:
            raise TimeoutError(f"job did not finish within {timeout:g}s: {job_id}")
        time.sleep(10)


def command_schedule_source(args: argparse.Namespace) -> int:
    settings = _settings_for_source(args)
    engine = create_db_engine(settings)
    job_store = create_job_store(settings, engine)
    service = create_resourcesat_ingestion_service(
        settings,
        engine,
        job_store=job_store,
    )
    result = run_source_job(
        plan=_manual_plan(args, settings),
        settings=settings,
        resourcesat_service=service,
        lock_registry=PostgresSourceAoiLockRegistry(engine),
    )
    payload = result.metadata()
    if result.status != "dispatched" or not result.job_id or args.dry_run:
        _json(payload)
        return 0 if result.status in {"planned", "dispatched"} else 1
    _json(_wait_for_job(job_store, result.job_id, args.wait_timeout))
    return 0


def command_discover_dates(args: argparse.Namespace) -> int:
    settings = _settings_for_source(args)
    engine = create_db_engine(settings)
    aoi_id = _canonical_aoi(args.aoi)
    aoi = create_aoi_repository(settings, engine).get(aoi_id)
    if aoi is None:
        raise ValueError(f"unknown AOI: {args.aoi}")
    profile = profile_for_source(args.source)
    candidates = BhoonidhiClient(settings).search(
        source_id=args.source,
        collection=profile.collection_id,
        intersects=aoi.geometry,
        aoi_bbox=aoi.bbox,
        date_start=date.fromisoformat(args.window_start),
        date_end=date.fromisoformat(args.window_end),
        max_items=args.limit,
    )
    eligible = [item for item in candidates if item.online and item.intersects_aoi]
    by_date: dict[str, int] = {}
    for item in eligible:
        if item.acquisition_at is not None:
            key = item.acquisition_at.date().isoformat()
            by_date[key] = by_date.get(key, 0) + 1
    _json(
        {
            "sourceId": args.source,
            "aoiId": aoi_id,
            "windowStart": args.window_start,
            "windowEnd": args.window_end,
            "searchedCount": len(candidates),
            "eligibleCount": len(eligible),
            "availableDates": [
                {"acquisitionDate": key, "sceneCount": by_date[key]}
                for key in sorted(by_date, reverse=True)
            ],
        }
    )
    return 0


def command_schedule_plan(args: argparse.Namespace) -> int:
    settings = Settings()
    plans = plan_due_sources(
        settings=settings,
        now=datetime.now(UTC),
        include_manual=True,
        dry_run=True,
    )
    if args.source:
        plans = [plan for plan in plans if plan.source_id == args.source]
    if args.aoi:
        aoi = _canonical_aoi(args.aoi)
        plans = [plan for plan in plans if plan.aoi_id == aoi]
    _json([asdict(plan) for plan in plans])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Akasha ingestion operational CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    source = subparsers.add_parser("schedule-source")
    source.add_argument("--source", required=True)
    source.add_argument("--aoi", required=True)
    source.add_argument("--window-start", required=True)
    source.add_argument("--window-end", required=True)
    source.add_argument("--window-days", type=int)
    source.add_argument("--limit", type=int, default=1000)
    source.add_argument("--max-downloads", type=int)
    source.add_argument("--min-coverage-percent", type=float)
    source.add_argument("--wait-timeout", type=float, default=21600)
    source.add_argument("--dry-run", action="store_true")
    source.add_argument("--approved-runtime", action="store_true")
    source.add_argument("--manual", action="store_true")
    source.add_argument("--json", action="store_true")
    for ignored in (
        "--lock-dir",
        "--base-dir",
        "--ledger-db-path",
        "--input-scale",
        "--polarizations",
    ):
        source.add_argument(ignored)
    source.add_argument("--overwrite", action="store_true")
    source.add_argument("--force", action="store_true")
    source.add_argument("--retain-raw-downloads", action="store_true")
    source.add_argument("--keep-intermediate", action="store_true")
    source.set_defaults(func=command_schedule_source)

    discover = subparsers.add_parser("discover-source-dates")
    discover.add_argument("--source", required=True)
    discover.add_argument("--aoi", required=True)
    discover.add_argument("--window-start", required=True)
    discover.add_argument("--window-end", required=True)
    discover.add_argument("--limit", type=int, default=1000)
    discover.add_argument("--approved-runtime", action="store_true")
    discover.add_argument("--json", action="store_true")
    discover.set_defaults(func=command_discover_dates)

    plan = subparsers.add_parser("schedule-plan")
    plan.add_argument("--source")
    plan.add_argument("--aoi")
    plan.add_argument("--window-days", type=int)
    plan.add_argument("--base-dir")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=command_schedule_plan)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
