from __future__ import annotations

from datetime import UTC, datetime, timedelta

from akasha.config import Settings
from akasha.jobs.store import Job, JobStatus
from akasha.schemas import (
    AnalyticsReadinessResponse,
    ReadinessIndexCoverage,
    ReadinessLastSuccessfulJob,
    ReadinessUnavailableReason,
)

REASON_MESSAGES = {
    "SOURCE_MISMATCH": "Requested source is not configured for the Sentinel-2 preload policy.",
    "AOI_MISMATCH": "Requested AOI is not configured for the Sentinel-2 preload policy.",
    "NO_SUCCESSFUL_PRELOAD_JOB": "No successful Sentinel-2 preload job is registered for this AOI.",
    "NO_PRELOAD_OUTPUTS": "No precomputed NDVI outputs are registered for this AOI.",
    "MISSING_INDEX_COVERAGE": "Precomputed outputs exist, but NDVI coverage is missing.",
    "PRELOAD_STALE": "Latest successful preload is older than the freshness threshold.",
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
        if source_id != self._settings.sentinel2_preload_source_id:
            return self._unavailable(source_id=source_id, aoi_id=aoi_id, code="SOURCE_MISMATCH")
        if aoi_id != self._settings.sentinel2_preload_aoi_id:
            return self._unavailable(source_id=source_id, aoi_id=aoi_id, code="AOI_MISMATCH")
        if self._scene_repository is None or self._raster_repository is None:
            return self._unavailable(
                source_id=source_id,
                aoi_id=aoi_id,
                code="NO_PRELOAD_OUTPUTS",
            )

        scenes = self._scene_repository.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        scene_ids = [scene.id for scene in scenes if scene.id is not None]
        ndvi_outputs = self._raster_repository.list_for_scene_ids(scene_ids, index_name="ndvi")
        ndvi_scene_ids = {output.scene_id for output in ndvi_outputs}
        scene_dates = {
            scene.acquisition_at.date()
            for scene in scenes
            if scene.acquisition_at is not None and scene.id is not None
        }
        available_dates = sorted(
            {
                scene.acquisition_at.date()
                for scene in scenes
                if scene.id in ndvi_scene_ids and scene.acquisition_at is not None
            },
            reverse=True,
        )
        index_coverage = {
            "NDVI": _coverage(
                date_count=len(available_dates),
                denominator=max(len(scene_dates), len(available_dates)),
            )
        }
        latest_scene_date = available_dates[0] if available_dates else None
        latest_output_at = _latest_output_created_at(ndvi_outputs)
        last_job = self._latest_successful_preload_job(source_id=source_id, aoi_id=aoi_id)
        last_job_completed_at = _completed_at(last_job) if last_job is not None else None
        freshness_at = _latest_datetime([latest_output_at, last_job_completed_at])
        stale_after = (
            freshness_at
            + timedelta(hours=self._settings.sentinel2_preload_freshness_max_age_hours)
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

        reasons: list[ReadinessUnavailableReason] = []
        if not available_dates:
            code = "NO_PRELOAD_OUTPUTS" if not scenes else "MISSING_INDEX_COVERAGE"
            reasons.append(_reason(code))
        if not available_dates and last_job is None:
            reasons.append(_reason("NO_SUCCESSFUL_PRELOAD_JOB"))

        status = "UNAVAILABLE"
        if available_dates and stale_after is not None:
            # Staleness is based only on NDVI output timestamps and output-producing full_pipeline
            # jobs. Metadata-only, mirror-only, partial, or no-output jobs never refresh readiness.
            if datetime.now(UTC) > stale_after:
                status = "STALE"
                reasons.append(_reason("PRELOAD_STALE"))
            else:
                status = "AVAILABLE"

        primary_reason = reasons[0] if reasons else None
        return AnalyticsReadinessResponse(
            status=status,
            sourceId=source_id,
            aoiId=aoi_id,
            providerRoute=self._settings.sentinel2_preload_provider_route,
            latestProcessedSceneDate=latest_scene_date,
            latestSuccessfulJobCompletedAt=(
                _format_utc(last_job_completed_at) if last_job_completed_at is not None else None
            ),
            lastSuccessfulJobAt=_format_utc(freshness_at) if freshness_at is not None else None,
            staleAfter=_format_utc(stale_after) if stale_after is not None else None,
            freshnessMaxAgeHours=self._settings.sentinel2_preload_freshness_max_age_hours,
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
        source_id: str,
        aoi_id: str,
        code: str,
    ) -> AnalyticsReadinessResponse:
        reason = _reason(code)
        return AnalyticsReadinessResponse(
            status="UNAVAILABLE",
            sourceId=source_id,
            aoiId=aoi_id,
            providerRoute=self._settings.sentinel2_preload_provider_route,
            latestProcessedSceneDate=None,
            latestSuccessfulJobCompletedAt=None,
            lastSuccessfulJobAt=None,
            staleAfter=None,
            freshnessMaxAgeHours=self._settings.sentinel2_preload_freshness_max_age_hours,
            availableDates=[],
            indexCoverage={"NDVI": _coverage(date_count=0, denominator=0)},
            lastSuccessfulJob=None,
            unavailableReasons=[reason],
            reasonCode=reason.code,
            reason=reason.message,
        )

    def _latest_successful_preload_job(self, *, source_id: str, aoi_id: str) -> Job | None:
        jobs = [
            job
            for job in self._job_store.list()
            if job.job_type == "sentinel2_backfill"
            and job.status == JobStatus.COMPLETED
            and job.source_id == source_id
            and job.aoi_id == aoi_id
            and _is_output_producing_full_pipeline(job)
        ]
        if not jobs:
            return None
        return max(jobs, key=lambda job: _completed_at(job) or job.updated_at)


def _coverage(*, date_count: int, denominator: int) -> ReadinessIndexCoverage:
    return ReadinessIndexCoverage(
        available=date_count > 0,
        dateCount=date_count,
        coveragePercent=round((date_count / denominator) * 100, 2) if denominator else 0.0,
    )


def _reason(code: str) -> ReadinessUnavailableReason:
    return ReadinessUnavailableReason(code=code, message=REASON_MESSAGES[code])


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
