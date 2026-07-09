from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Literal

from akasha.config import RuntimeBackend, Settings, validate_resourcesat_runtime_roots
from akasha.jobs.store import Job
from akasha.providers.bhoonidhi import redact_string
from akasha.providers.contracts import ProviderDataError
from akasha.scheduler.locks import source_aoi_lock_name
from akasha.scheduler.planner import PlannedSourceRun
from akasha.scheduler.source_registry import SourceState, source_state_by_id
from akasha.schemas import SyncRequest

SchedulerRunStatus = Literal["planned", "blocked", "dispatched", "failed"]
LOGGER = getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchedulerRunResult:
    status: SchedulerRunStatus
    source_id: str
    aoi_id: str
    provider_route: str
    dry_run: bool
    reason: str
    planned_stages: tuple[str, ...]
    thresholds: dict[str, float | int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    job_id: str | None = None
    error_category: str | None = None

    def metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sourceId": self.source_id,
            "aoiId": self.aoi_id,
            "providerRoute": self.provider_route,
            "dryRun": self.dry_run,
            "reason": self.reason,
            "plannedStages": list(self.planned_stages),
            "thresholds": dict(self.thresholds),
            "counts": dict(self.counts),
            "jobId": self.job_id,
            "errorCategory": self.error_category,
        }


def run_source_job(
    *,
    plan: PlannedSourceRun,
    settings: Settings,
    resourcesat_service,
    lock_registry,
) -> SchedulerRunResult:
    source = source_state_by_id(settings, plan.source_id)
    planned_stages = _planned_stages(plan.mode)
    if source is None:
        return _emit(
            _blocked(
                plan,
                "unsupported ResourceSat source",
                planned_stages,
                "unsupported_source",
            )
        )
    gate_reason = _source_gate(source, plan)
    if gate_reason is not None:
        return _emit(_blocked(plan, gate_reason, planned_stages, "source_gated"))

    with lock_registry.acquire(source_id=plan.source_id, aoi_id=plan.aoi_id) as acquired:
        if not acquired:
            lock_name = source_aoi_lock_name(source_id=plan.source_id, aoi_id=plan.aoi_id)
            return _emit(
                _blocked(
                    plan,
                    f"source/AOI lock is already held: {lock_name}",
                    planned_stages,
                    "lock_held",
                )
            )
        if plan.dry_run:
            return _emit(
                SchedulerRunResult(
                    status="planned",
                    source_id=plan.source_id,
                    aoi_id=plan.aoi_id,
                    provider_route=plan.provider_route,
                    dry_run=True,
                    reason="dry run only; no provider, object-store, raster, or pgSTAC mutation",
                    planned_stages=planned_stages,
                    thresholds=dict(plan.thresholds or {}),
                    counts={
                        "plannedStages": len(planned_stages),
                        "providerCalls": 0,
                        "mutations": 0,
                    },
                )
            )
        try:
            _require_approved_runtime(settings)
            _require_disk_headroom(settings)
            job = resourcesat_service.start_backfill(_sync_request(plan))
        except Exception as exc:
            return _emit(
                SchedulerRunResult(
                    status="failed",
                    source_id=plan.source_id,
                    aoi_id=plan.aoi_id,
                    provider_route=plan.provider_route,
                    dry_run=False,
                    reason=_safe_failure_reason(exc),
                    planned_stages=planned_stages,
                    thresholds=dict(plan.thresholds or {}),
                    counts={"plannedStages": len(planned_stages)},
                    error_category=_classify_failure(exc),
                )
            )
    return _emit(_dispatched(plan, job, planned_stages))


def _source_gate(source: SourceState, plan: PlannedSourceRun) -> str | None:
    if plan.decision != "due":
        return plan.reason
    if source.lifecycle_state == "disabled":
        return "source lifecycle is disabled"
    if source.lifecycle_state == "manual" and not plan.dry_run:
        return "manual source requires a dry-run or scheduled enablement before live execution"
    if plan.aoi_id not in {aoi.aoi_id for aoi in source.default_aois}:
        return "AOI is outside the configured source scope"
    return None


def _require_approved_runtime(settings: Settings) -> None:
    if not settings.bhoonidhi_approved_runtime_required:
        return
    if settings.bhoonidhi_approved_runtime:
        return
    raise RuntimeError("Bhoonidhi ResourceSat live jobs require approved runtime")


def _require_disk_headroom(settings: Settings) -> None:
    validate_resourcesat_runtime_roots(
        settings,
        dry_run=settings.runtime_backend == RuntimeBackend.MEMORY,
    )
    required = settings.source_mirror_required_headroom_bytes
    if required <= 0:
        return
    free = shutil.disk_usage(_disk_usage_path(settings.scratch_dir)).free
    if free < required:
        raise RuntimeError(
            f"insufficient ResourceSat disk headroom: required {required} bytes, "
            f"available {free} bytes"
        )


def _sync_request(plan: PlannedSourceRun) -> SyncRequest:
    return SyncRequest(
        source_id=plan.source_id,
        provider_route=plan.provider_route,
        aoi_id=plan.aoi_id,
        date_start=plan.date_start,
        date_end=plan.date_end,
        job_type="resourcesat_backfill",
        mode=plan.mode,
    )


def _planned_stages(mode: str) -> tuple[str, ...]:
    stages = (
        "provider_search",
        "raw_download",
        "prepare_scene",
        "scene_validation",
        "composite",
        "composite_validation",
        "index_generation",
        "pgstac_registration",
        "readiness_refresh",
        "cleanup",
    )
    if mode == "full_pipeline":
        return stages
    return stages[:1]


def _blocked(
    plan: PlannedSourceRun,
    reason: str,
    planned_stages: tuple[str, ...],
    category: str,
) -> SchedulerRunResult:
    return SchedulerRunResult(
        status="blocked",
        source_id=plan.source_id,
        aoi_id=plan.aoi_id,
        provider_route=plan.provider_route,
        dry_run=plan.dry_run,
        reason=reason,
        planned_stages=planned_stages,
        thresholds=dict(plan.thresholds or {}),
        error_category=category,
    )


def _dispatched(
    plan: PlannedSourceRun,
    job: Job,
    planned_stages: tuple[str, ...],
) -> SchedulerRunResult:
    return SchedulerRunResult(
        status="dispatched",
        source_id=plan.source_id,
        aoi_id=plan.aoi_id,
        provider_route=plan.provider_route,
        dry_run=False,
        reason=f"ResourceSat job {job.status.value}",
        planned_stages=planned_stages,
        thresholds=dict(plan.thresholds or {}),
        counts={"plannedStages": len(planned_stages)},
        job_id=job.job_id,
    )


def _classify_failure(exc: Exception) -> str:
    if isinstance(exc, ProviderDataError):
        return exc.category.value
    text = str(exc).lower()
    if "approved runtime" in text:
        return "approved_runtime_required"
    if "headroom" in text or "disk" in text:
        return "insufficient_headroom"
    if "lock" in text:
        return "lock_failed"
    return "unknown"


def _safe_failure_reason(exc: Exception) -> str:
    text = str(exc)
    normalized = text.lower()
    if "resourcesat runtime root" in normalized or "approved data root" in normalized:
        return "ResourceSat runtime root failed approved-root validation"
    return redact_string(text)


def _emit(result: SchedulerRunResult) -> SchedulerRunResult:
    LOGGER.info("resourcesat_scheduler_run", extra={"scheduler_run": result.metadata()})
    return result


def _disk_usage_path(path: Path) -> Path:
    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    return current
