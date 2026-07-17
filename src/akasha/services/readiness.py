from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from akasha.config import Settings
from akasha.jobs.store import Job, JobStatus
from akasha.processing.resourcesat import RESOURCESAT_PROFILES
from akasha.schemas import (
    AnalyticsReadinessResponse,
    ReadinessIndexCoverage,
    ReadinessLastSuccessfulJob,
    ReadinessUnavailableReason,
)

RESOURCESAT_COMPOSITE_OUTPUT_KIND = "resource_sat_composite"


@dataclass(frozen=True, slots=True)
class ReadinessPolicy:
    source_id: str
    aoi_id: str
    provider_route: str
    job_type: str
    required_indices: tuple[str, ...]
    freshness_max_age_hours: int
    reason_messages: dict[str, str]
    min_coverage_percent: float | None = None
    enabled: bool = True
    require_successful_job: bool = False


SENTINEL_REASON_MESSAGES = {
    "SOURCE_MISMATCH": "Requested source is not configured for the Sentinel-2 preload policy.",
    "AOI_MISMATCH": "Requested AOI is not configured for the Sentinel-2 preload policy.",
    "NO_SUCCESSFUL_PRELOAD_JOB": "No successful Sentinel-2 preload job is registered for this AOI.",
    "NO_PRELOAD_OUTPUTS": "No precomputed NDVI outputs are registered for this AOI.",
    "MISSING_INDEX_COVERAGE": "Precomputed outputs exist, but NDVI coverage is missing.",
    "PRELOAD_STALE": "Latest successful preload is older than the freshness threshold.",
    "SOURCE_NOT_ENABLED": "Requested source is not enabled for analytics readiness.",
    "LOW_COVERAGE": "Latest outputs do not satisfy the configured coverage threshold.",
}

RESOURCESAT_REASON_MESSAGES = {
    "SOURCE_MISMATCH": "Requested source is not configured for ResourceSat readiness.",
    "AOI_MISMATCH": "Requested AOI is not configured for ResourceSat readiness.",
    "NO_SUCCESSFUL_RESOURCE_SAT_JOB": (
        "No successful ResourceSat full-pipeline job is registered for this AOI."
    ),
    "NO_RESOURCE_SAT_OUTPUTS": "No ResourceSat derived outputs are registered for this AOI.",
    "MISSING_INDEX_COVERAGE": "ResourceSat outputs exist, but required index coverage is missing.",
    "RESOURCE_SAT_STALE": (
        "Latest successful ResourceSat output is older than the freshness threshold."
    ),
    "LOW_COVERAGE": "ResourceSat outputs do not satisfy the configured coverage threshold.",
    "SOURCE_NOT_ENABLED": "Requested ResourceSat source is not enabled for analytics readiness.",
}


class ReadinessService:
    def __init__(
        self,
        *,
        settings: Settings,
        job_store,
        scene_repository=None,
        raster_repository=None,
    ) -> None:
        self._settings = settings
        self._job_store = job_store
        self._scene_repository = scene_repository
        self._raster_repository = raster_repository

    def readiness(self, *, source_id: str, aoi_id: str) -> AnalyticsReadinessResponse:
        policy = self._policy(source_id)
        if policy is None:
            fallback = self._sentinel_policy()
            return self._unavailable(
                policy=fallback,
                source_id=source_id,
                aoi_id=aoi_id,
                code="SOURCE_MISMATCH",
            )
        if not policy.enabled:
            return self._unavailable(
                policy=policy,
                source_id=source_id,
                aoi_id=aoi_id,
                code="SOURCE_NOT_ENABLED",
            )
        if aoi_id != policy.aoi_id:
            return self._unavailable(
                policy=policy,
                source_id=source_id,
                aoi_id=aoi_id,
                code="AOI_MISMATCH",
            )
        if self._scene_repository is None or self._raster_repository is None:
            return self._unavailable(
                policy=policy,
                source_id=source_id,
                aoi_id=aoi_id,
                code=_no_outputs_code(policy),
            )

        scenes = _readiness_scenes(
            self._scene_repository.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id),
            policy,
        )
        scene_ids = [scene.id for scene in scenes if scene.id is not None]
        outputs_by_index = {
            index_name: self._raster_repository.list_for_scene_ids(
                scene_ids,
                index_name=index_name,
            )
            for index_name in policy.required_indices
        }
        registered_outputs = self._raster_repository.list_for_scene_ids(scene_ids)
        scene_dates = {
            scene.acquisition_at.date()
            for scene in scenes
            if scene.acquisition_at is not None and scene.id is not None
        }
        index_date_sets = {
            index_name: {
                scene.acquisition_at.date()
                for scene in scenes
                if scene.id in {output.scene_id for output in outputs}
                and scene.acquisition_at is not None
                and _coverage_ok(scene, policy)
            }
            for index_name, outputs in outputs_by_index.items()
        }
        available_date_set = _intersection(index_date_sets.values())
        available_dates = sorted(available_date_set, reverse=True)
        index_coverage = {
            _coverage_key(index_name): _coverage(
                date_count=len(index_date_sets[index_name]),
                denominator=max(len(scene_dates), len(index_date_sets[index_name])),
            )
            for index_name in policy.required_indices
        }
        latest_scene_date = available_dates[0] if available_dates else None
        all_outputs = [output for outputs in outputs_by_index.values() for output in outputs]
        latest_output_at = _latest_output_created_at(all_outputs)
        last_job = self._latest_successful_job(policy=policy, source_id=source_id, aoi_id=aoi_id)
        last_job_completed_at = _completed_at(last_job) if last_job is not None else None
        freshness_at = _latest_datetime([latest_output_at, last_job_completed_at])
        stale_after = (
            freshness_at + timedelta(hours=policy.freshness_max_age_hours)
            if freshness_at is not None
            else None
        )
        last_job_payload = (
            ReadinessLastSuccessfulJob(
                jobId=last_job.job_id,
                completedAt=_format_utc(last_job_completed_at),
            )
            if last_job is not None and last_job_completed_at is not None
            else None
        )

        reasons = self._unavailable_reasons(
            policy=policy,
            scenes=scenes,
            available_dates=available_dates,
            outputs=registered_outputs,
            last_job=last_job,
        )
        status = "UNAVAILABLE"
        if available_dates and stale_after is not None and not reasons:
            if datetime.now(UTC) > stale_after:
                status = "STALE"
                reasons.append(_reason(policy, _stale_code(policy)))
            else:
                status = "AVAILABLE"

        primary_reason = reasons[0] if reasons else None
        return AnalyticsReadinessResponse(
            status=status,
            sourceId=source_id,
            aoiId=aoi_id,
            providerRoute=policy.provider_route,
            latestProcessedSceneDate=latest_scene_date,
            latestSuccessfulJobCompletedAt=(
                _format_utc(last_job_completed_at) if last_job_completed_at is not None else None
            ),
            lastSuccessfulJobAt=_format_utc(freshness_at) if freshness_at is not None else None,
            staleAfter=_format_utc(stale_after) if stale_after is not None else None,
            freshnessMaxAgeHours=policy.freshness_max_age_hours,
            availableDates=available_dates,
            indexCoverage=index_coverage,
            lastSuccessfulJob=last_job_payload,
            unavailableReasons=reasons,
            reasonCode=primary_reason.code if primary_reason else None,
            reason=primary_reason.message if primary_reason else None,
        )

    def _unavailable(
        self,
        *,
        policy: ReadinessPolicy,
        source_id: str,
        aoi_id: str,
        code: str,
    ) -> AnalyticsReadinessResponse:
        reason = _reason(policy, code)
        return AnalyticsReadinessResponse(
            status="UNAVAILABLE",
            sourceId=source_id,
            aoiId=aoi_id,
            providerRoute=policy.provider_route,
            latestProcessedSceneDate=None,
            latestSuccessfulJobCompletedAt=None,
            lastSuccessfulJobAt=None,
            staleAfter=None,
            freshnessMaxAgeHours=policy.freshness_max_age_hours,
            availableDates=[],
            indexCoverage={
                _coverage_key(index_name): _coverage(date_count=0, denominator=0)
                for index_name in policy.required_indices
            },
            lastSuccessfulJob=None,
            unavailableReasons=[reason],
            reasonCode=reason.code,
            reason=reason.message,
        )

    def _unavailable_reasons(
        self,
        *,
        policy: ReadinessPolicy,
        scenes: list[object],
        available_dates: list[object],
        outputs: list[object],
        last_job: Job | None,
    ) -> list[ReadinessUnavailableReason]:
        reasons: list[ReadinessUnavailableReason] = []
        if policy.require_successful_job and last_job is None:
            reasons.append(_reason(policy, _no_successful_job_code(policy)))
        if available_dates:
            return reasons
        if not outputs:
            reasons.append(_reason(policy, _no_outputs_code(policy)))
        elif scenes and policy.min_coverage_percent is not None and not any(
            _coverage_ok(scene, policy) for scene in scenes
        ):
            reasons.append(_reason(policy, "LOW_COVERAGE"))
        else:
            reasons.append(_reason(policy, "MISSING_INDEX_COVERAGE"))
        if not policy.require_successful_job and last_job is None:
            reasons.append(_reason(policy, _no_successful_job_code(policy)))
        return reasons

    def _latest_successful_job(
        self,
        *,
        policy: ReadinessPolicy,
        source_id: str,
        aoi_id: str,
    ) -> Job | None:
        jobs = [
            job
            for job in self._job_store.list()
            if job.job_type == policy.job_type
            and job.status == JobStatus.COMPLETED
            and job.source_id == source_id
            and job.aoi_id == aoi_id
            and _is_output_producing_full_pipeline(job)
        ]
        if not jobs:
            return None
        return max(jobs, key=lambda job: _completed_at(job) or job.updated_at)

    def _policy(self, source_id: str) -> ReadinessPolicy | None:
        policies = self._policies()
        return policies.get(source_id)

    def _policies(self) -> dict[str, ReadinessPolicy]:
        policies = {self._settings.sentinel2_preload_source_id: self._sentinel_policy()}
        policies.update(self._resourcesat_policies())
        return policies

    def _sentinel_policy(self) -> ReadinessPolicy:
        return ReadinessPolicy(
            source_id=self._settings.sentinel2_preload_source_id,
            aoi_id=self._settings.sentinel2_preload_aoi_id,
            provider_route=self._settings.sentinel2_preload_provider_route,
            job_type="sentinel2_backfill",
            required_indices=("ndvi",),
            freshness_max_age_hours=self._settings.sentinel2_preload_freshness_max_age_hours,
            reason_messages=SENTINEL_REASON_MESSAGES,
        )

    def _resourcesat_policies(self) -> dict[str, ReadinessPolicy]:
        return {
            self._settings.resourcesat_liss3_preload_source_id: self._resourcesat_policy(
                source_id=self._settings.resourcesat_liss3_preload_source_id,
                aoi_id=self._settings.resourcesat_liss3_preload_aoi_id,
                provider_route=self._settings.resourcesat_liss3_preload_provider_route,
                freshness_max_age_hours=(
                    self._settings.resourcesat_liss3_preload_freshness_max_age_hours
                ),
                min_coverage_percent=(
                    self._settings.resourcesat_liss3_composite_min_coverage_percent
                ),
                enabled=self._settings.resourcesat_liss3_readiness_enabled,
                required_indices=self._settings.resourcesat_liss3_readiness_required_indices,
            ),
            self._settings.resourcesat_liss4_preload_source_id: self._resourcesat_policy(
                source_id=self._settings.resourcesat_liss4_preload_source_id,
                aoi_id=self._settings.resourcesat_liss4_preload_aoi_id,
                provider_route=self._settings.resourcesat_liss4_preload_provider_route,
                freshness_max_age_hours=(
                    self._settings.resourcesat_liss4_preload_freshness_max_age_hours
                ),
                min_coverage_percent=(
                    self._settings.resourcesat_liss4_composite_min_coverage_percent
                ),
                enabled=self._settings.resourcesat_liss4_readiness_enabled,
                required_indices=self._settings.resourcesat_liss4_readiness_required_indices,
            ),
            self._settings.resourcesat_awifs_preload_source_id: self._resourcesat_policy(
                source_id=self._settings.resourcesat_awifs_preload_source_id,
                aoi_id=self._settings.resourcesat_awifs_preload_aoi_id,
                provider_route=self._settings.resourcesat_awifs_preload_provider_route,
                freshness_max_age_hours=(
                    self._settings.resourcesat_awifs_preload_freshness_max_age_hours
                ),
                min_coverage_percent=(
                    self._settings.resourcesat_awifs_composite_min_coverage_percent
                ),
                enabled=self._settings.resourcesat_awifs_readiness_enabled,
                required_indices=self._settings.resourcesat_awifs_readiness_required_indices,
            ),
        }

    def _resourcesat_policy(
        self,
        *,
        source_id: str,
        aoi_id: str,
        provider_route: str,
        freshness_max_age_hours: int,
        min_coverage_percent: float,
        enabled: bool,
        required_indices: tuple[str, ...],
    ) -> ReadinessPolicy:
        profile = RESOURCESAT_PROFILES[source_id]
        normalized_indices = _required_indices(profile.supported_indices, required_indices)
        return ReadinessPolicy(
            source_id=source_id,
            aoi_id=aoi_id,
            provider_route=provider_route,
            job_type="resourcesat_backfill",
            required_indices=normalized_indices,
            freshness_max_age_hours=freshness_max_age_hours,
            reason_messages=RESOURCESAT_REASON_MESSAGES,
            min_coverage_percent=min_coverage_percent,
            enabled=enabled,
            require_successful_job=True,
        )


def _coverage(*, date_count: int, denominator: int) -> ReadinessIndexCoverage:
    return ReadinessIndexCoverage(
        available=date_count > 0,
        dateCount=date_count,
        coveragePercent=round((date_count / denominator) * 100, 2) if denominator else 0.0,
    )


def _reason(policy: ReadinessPolicy, code: str) -> ReadinessUnavailableReason:
    return ReadinessUnavailableReason(code=code, message=policy.reason_messages[code])


def _coverage_key(index_name: str) -> str:
    return index_name.upper()


def _completed_at(job: Job | None) -> datetime | None:
    if job is None:
        return None
    return job.completed_at or job.updated_at


def _latest_output_created_at(outputs: list[object]) -> datetime | None:
    timestamps = [
        output.created_at.astimezone(UTC)
        for output in outputs
        if getattr(output, "created_at", None) is not None
    ]
    return max(timestamps) if timestamps else None


def _latest_datetime(values: list[datetime | None]) -> datetime | None:
    timestamps = [value.astimezone(UTC) for value in values if value is not None]
    return max(timestamps) if timestamps else None


def _is_output_producing_full_pipeline(job: Job) -> bool:
    if job.result_metadata.get("mode") != "full_pipeline":
        return False
    summary = job.result_metadata.get("backfill_summary")
    if not isinstance(summary, dict):
        return False
    processed_count = int(summary.get("processed_count") or 0)
    failed_count = int(summary.get("failed_count") or 0)
    return processed_count > 0 and failed_count == 0


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _intersection(values: object) -> set[object]:
    sets = [set(value) for value in values]
    if not sets:
        return set()
    result = sets[0]
    for value in sets[1:]:
        result &= value
    return result


def _readiness_scenes(scenes: list[object], policy: ReadinessPolicy) -> list[object]:
    if policy.job_type != "resourcesat_backfill":
        return scenes
    return [
        scene
        for scene in scenes
        if getattr(scene, "status", None) == "composited"
        or getattr(scene, "provider_metadata", {}).get("output_kind")
        == RESOURCESAT_COMPOSITE_OUTPUT_KIND
    ]


def _coverage_ok(scene: object, policy: ReadinessPolicy) -> bool:
    # Source readiness reports integrity-valid processed dates. Spatial suitability
    # is evaluated later against the selected field polygon.
    return True


def _no_outputs_code(policy: ReadinessPolicy) -> str:
    return (
        "NO_RESOURCE_SAT_OUTPUTS"
        if policy.job_type == "resourcesat_backfill"
        else "NO_PRELOAD_OUTPUTS"
    )


def _no_successful_job_code(policy: ReadinessPolicy) -> str:
    return (
        "NO_SUCCESSFUL_RESOURCE_SAT_JOB"
        if policy.job_type == "resourcesat_backfill"
        else "NO_SUCCESSFUL_PRELOAD_JOB"
    )


def _stale_code(policy: ReadinessPolicy) -> str:
    return "RESOURCE_SAT_STALE" if policy.job_type == "resourcesat_backfill" else "PRELOAD_STALE"


def _required_indices(
    supported_indices: tuple[str, ...],
    required_indices: tuple[str, ...],
) -> tuple[str, ...]:
    normalized = tuple(index.lower() for index in required_indices)
    unsupported = sorted(set(normalized) - set(supported_indices))
    if unsupported:
        raise ValueError(f"unsupported readiness indices: {', '.join(unsupported)}")
    if not normalized:
        raise ValueError("readiness requires at least one index")
    return normalized
