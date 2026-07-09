from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from akasha.config import Settings
from akasha.scheduler.source_registry import (
    SourceAoiState,
    SourceState,
    resourcesat_source_registry,
)

PlannerDecision = Literal["due", "not_due", "disabled"]


@dataclass(frozen=True, slots=True)
class PlannedSourceRun:
    source_id: str
    aoi_id: str
    provider_route: str
    date_start: date
    date_end: date
    mode: Literal["full_pipeline"] = "full_pipeline"
    decision: PlannerDecision = "due"
    reason: str = "scheduled source is due"
    dry_run: bool = False
    thresholds: dict[str, float | int] | None = None


def plan_due_sources(
    *,
    settings: Settings,
    now: datetime | None = None,
    last_success_by_source_aoi: dict[tuple[str, str], datetime] | None = None,
    include_manual: bool = False,
    dry_run: bool = False,
) -> list[PlannedSourceRun]:
    effective_now = (now or datetime.now(UTC)).astimezone(UTC)
    last_success = last_success_by_source_aoi or {}
    plans: list[PlannedSourceRun] = []
    for source in resourcesat_source_registry(settings):
        for aoi in source.default_aois:
            plans.append(
                _plan_source_aoi(
                    source=source,
                    aoi=aoi,
                    now=effective_now,
                    last_success=last_success.get((source.source_id, aoi.aoi_id)),
                    include_manual=include_manual,
                    dry_run=dry_run,
                )
            )
    return plans


def _plan_source_aoi(
    *,
    source: SourceState,
    aoi: SourceAoiState,
    now: datetime,
    last_success: datetime | None,
    include_manual: bool,
    dry_run: bool,
) -> PlannedSourceRun:
    if source.schedule_state == "disabled":
        return _plan(
            source,
            aoi,
            now=now,
            decision="disabled",
            reason="source schedule is disabled",
            dry_run=dry_run,
        )
    if source.schedule_state == "manual" and not include_manual:
        return _plan(
            source,
            aoi,
            now=now,
            decision="not_due",
            reason="source is manual-only",
            dry_run=dry_run,
        )
    if last_success is not None:
        next_due_at = last_success.astimezone(UTC) + timedelta(days=aoi.refresh_days)
        if now < next_due_at:
            return _plan(
                source,
                aoi,
                now=now,
                decision="not_due",
                reason=f"next run is due at {next_due_at.isoformat()}",
                dry_run=dry_run,
            )
    return _plan(
        source,
        aoi,
        now=now,
        decision="due",
        reason="scheduled source is due" if source.schedule_state == "scheduled" else "manual run",
        dry_run=dry_run,
    )


def _plan(
    source: SourceState,
    aoi: SourceAoiState,
    *,
    now: datetime,
    decision: PlannerDecision,
    reason: str,
    dry_run: bool,
) -> PlannedSourceRun:
    return PlannedSourceRun(
        source_id=source.source_id,
        aoi_id=aoi.aoi_id,
        provider_route=aoi.provider_route,
        date_start=(now - timedelta(days=aoi.date_window_days)).date(),
        date_end=now.date(),
        decision=decision,
        reason=reason,
        dry_run=dry_run,
        thresholds={
            "maxDownloads": aoi.max_downloads,
            "minCoveragePercent": aoi.min_coverage_percent,
            "compositeWindowDays": aoi.composite_window_days,
            "freshnessMaxAgeHours": aoi.freshness_max_age_hours,
        },
    )
