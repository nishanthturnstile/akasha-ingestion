from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from akasha.config import Environment
from akasha.jobs.store import Job
from akasha.processing.eos04 import EOS04_COLLECTION_ID, EOS04_SOURCE_ID
from akasha.processing.resourcesat import RESOURCESAT_SOURCE_COLLECTIONS, RESOURCESAT_SOURCE_IDS

T = TypeVar("T")
FIELD_DATES_MAX_DATES = 64
FIELD_DATES_MAX_CLOUD_PERCENTAGE = 20.0


class ErrorPayload(BaseModel):
    code: str
    message: str


class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: ErrorPayload | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    environment: Environment
    services: dict[str, str]


class SourceResponse(BaseModel):
    source_id: str
    catalog_slug: str
    provider_adapter: str
    instrument_mode: str
    analysis_level: str
    schedule_state: str
    product_exposure: str
    supported_indices: list[str] = Field(default_factory=list)


class NaturalSourceDate(BaseModel):
    acquisitionDate: date
    datetime: datetime
    tileAvailable: bool
    sceneCount: int
    bounds: list[float] | None = None
    polarizations: list[str] = Field(default_factory=list)
    unavailableReason: str | None = None


class SourceDatesResponse(BaseModel):
    sourceId: str
    aoiId: str
    dates: list[NaturalSourceDate]


class SyncRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1)
    aoi_id: str = Field(min_length=1)
    date_start: date
    date_end: date
    job_type: Literal[
        "mock_sync",
        "sentinel2_backfill",
        "resourcesat_backfill",
        "eos04_backfill",
    ] = "mock_sync"
    provider_route: str | None = Field(default=None, min_length=1)
    mode: Literal[
        "metadata_only",
        "mirror_only",
        "download_only",
        "prepare_only",
        "composite_only",
        "full_pipeline",
    ] = "metadata_only"

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.date_end < self.date_start:
            raise ValueError("date_end must be on or after date_start")
        if self.job_type == "sentinel2_backfill":
            if self.source_id != "sentinel-2-l2a":
                raise ValueError("sentinel2_backfill requires source_id sentinel-2-l2a")
            if self.provider_route != "earthsearch:sentinel-2-l2a":
                raise ValueError(
                    "sentinel2_backfill requires provider_route earthsearch:sentinel-2-l2a"
                )
            if self.mode not in {"metadata_only", "mirror_only", "full_pipeline"}:
                raise ValueError(
                    "sentinel2_backfill mode must be metadata_only, mirror_only, or full_pipeline"
                )
        elif self.job_type == "resourcesat_backfill":
            if self.source_id not in RESOURCESAT_SOURCE_IDS:
                raise ValueError("resourcesat_backfill requires a ResourceSat source_id")
            expected_route = f"bhoonidhi:{RESOURCESAT_SOURCE_COLLECTIONS[self.source_id]}"
            if self.provider_route != expected_route:
                raise ValueError(
                    f"resourcesat_backfill requires provider_route {expected_route}"
                )
            if self.mode not in {
                "metadata_only",
                "download_only",
                "prepare_only",
                "composite_only",
                "full_pipeline",
            }:
                raise ValueError(
                    "resourcesat_backfill mode must be metadata_only, download_only, "
                    "prepare_only, composite_only, or full_pipeline"
                )
        elif self.job_type == "eos04_backfill":
            if self.source_id != EOS04_SOURCE_ID:
                raise ValueError(f"eos04_backfill requires source_id {EOS04_SOURCE_ID}")
            expected_route = f"bhoonidhi:{EOS04_COLLECTION_ID}"
            if self.provider_route != expected_route:
                raise ValueError(f"eos04_backfill requires provider_route {expected_route}")
            if self.mode not in {
                "metadata_only",
                "download_only",
                "prepare_only",
                "full_pipeline",
            }:
                raise ValueError(
                    "eos04_backfill mode must be metadata_only, download_only, prepare_only, "
                    "or full_pipeline"
                )
        elif self.provider_route is not None:
            raise ValueError("provider_route is not supported for mock_sync")
        return self


class JobResponse(BaseModel):
    job_id: str
    job_type: str
    idempotency_key: str
    status: str
    source_id: str
    aoi_id: str
    date_start: str
    date_end: str
    asset_ref: str | None = None
    checksum_sha256: str | None = None
    result_metadata: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: Job) -> JobResponse:
        return cls(
            job_id=job.job_id,
            job_type=job.job_type,
            idempotency_key=job.idempotency_key,
            status=job.status.value,
            source_id=job.source_id,
            aoi_id=job.aoi_id,
            date_start=job.date_start,
            date_end=job.date_end,
            asset_ref=f"asset:{job.job_id}" if job.object_path else None,
            checksum_sha256=job.checksum_sha256,
            result_metadata=job.result_metadata,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class FieldIndexRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    geometry: dict[str, Any]
    sourceId: str = Field(default="sentinel-2-l2a", min_length=1)
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    index: Literal["NDVI", "MSAVI", "NDMI", "NDBI", "NDRE", "RECI", "NDWI_GREEN_NIR"]
    date: date
    fallbackPolicy: Literal["nearest_valid_scene"] = "nearest_valid_scene"
    maxCloudPercentage: float = Field(default=20.0, ge=0, le=100)
    fieldId: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        geometry_type = self.geometry.get("type")
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("geometry must be a Polygon or MultiPolygon")
        coordinates = self.geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("geometry coordinates are required")
        return self


class FieldDatesRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    geometry: dict[str, Any]
    sourceId: str = Field(default="sentinel-2-l2a", min_length=1)
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    index: Literal["NDVI", "MSAVI", "NDMI", "NDBI", "NDRE", "RECI", "NDWI_GREEN_NIR"]
    dates: list[date] = Field(min_length=1, max_length=FIELD_DATES_MAX_DATES)
    maxCloudPercentage: float = Field(
        default=FIELD_DATES_MAX_CLOUD_PERCENTAGE,
        ge=0,
        le=FIELD_DATES_MAX_CLOUD_PERCENTAGE,
    )

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        geometry_type = self.geometry.get("type")
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("geometry must be a Polygon or MultiPolygon")
        coordinates = self.geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("geometry coordinates are required")
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("dates must be unique")
        return self


class FieldDateAvailability(BaseModel):
    acquisitionDate: date
    available: bool
    selectedSceneDate: date | None = None
    usablePixelPercentage: float | None = Field(default=None, ge=0, le=100)
    cloudPercentage: float | None = Field(
        default=None,
        ge=0,
        le=FIELD_DATES_MAX_CLOUD_PERCENTAGE,
    )
    fieldCoveragePercentage: float | None = Field(default=None, ge=0, le=100)
    shadowPercentage: float | None = Field(default=None, ge=0, le=100)
    obscuredPercentage: float | None = Field(default=None, ge=0, le=100)
    validPixelCount: int = Field(default=0, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.available:
            if self.selectedSceneDate != self.acquisitionDate:
                raise ValueError("available field dates must select the exact acquisition date")
            if self.usablePixelPercentage is None or self.validPixelCount <= 0:
                raise ValueError("available field dates require usable pixels")
            if any(
                value is None
                for value in (
                    self.cloudPercentage,
                    self.fieldCoveragePercentage,
                    self.shadowPercentage,
                    self.obscuredPercentage,
                )
            ):
                raise ValueError("available field dates require field quality percentages")
            if self.reason is not None:
                raise ValueError("available field dates cannot include an unavailable reason")
            return self
        if self.selectedSceneDate is not None or self.usablePixelPercentage is not None:
            raise ValueError("unavailable field dates cannot include selected-scene metrics")
        quality_values = (
            self.cloudPercentage,
            self.fieldCoveragePercentage,
            self.shadowPercentage,
            self.obscuredPercentage,
        )
        if any(value is not None for value in quality_values) or self.validPixelCount != 0:
            raise ValueError("unavailable field dates cannot include raster metrics")
        if not self.reason or not self.reason.strip():
            raise ValueError("unavailable field dates require a reason")
        return self


class FieldDatesResponse(BaseModel):
    sourceId: str
    index: str
    dates: list[FieldDateAvailability]


class FieldIndexUnavailableResponse(BaseModel):
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    index: str
    requestedDate: date
    reason: str
    searchedSources: list[str]


class FieldIndexResolution(BaseModel):
    nativeMeters: float
    processingMeters: float
    displayMeters: float


class FieldIndexStatistics(BaseModel):
    min: float | None
    max: float | None
    mean: float | None
    median: float | None
    stdDev: float | None
    usablePixelPercentage: float
    cloudPercentage: float | None
    fieldCoveragePercentage: float | None = None
    shadowPercentage: float | None = None
    obscuredPercentage: float | None = None


class FieldIndexSelection(BaseModel):
    windowDays: int
    rule: str
    validPixelCount: int


class FieldIndexVisualization(BaseModel):
    displayProfile: str | None
    thresholdProfile: str | None
    legend: list[dict[str, Any]] = Field(default_factory=list)


class FieldIndexQuality(BaseModel):
    status: Literal["GOOD", "WARN", "UNAVAILABLE"]
    reason: str
    warnings: list[str] = Field(default_factory=list)


class FieldIndexAvailableResponse(BaseModel):
    status: Literal["AVAILABLE"] = "AVAILABLE"
    queryId: str
    fieldId: str | None = None
    index: str
    requestedDate: date
    selectedSceneDate: date
    source: str
    providerRoute: str
    resolution: FieldIndexResolution
    layerId: str
    tileUrl: str
    statsUrl: str
    overlayUrl: str | None = None
    pointUrl: str | None = None
    selection: FieldIndexSelection
    statistics: FieldIndexStatistics
    classStatistics: list[dict[str, Any]] = Field(default_factory=list)
    visualization: FieldIndexVisualization
    versions: dict[str, str]
    quality: FieldIndexQuality


class FieldIndexPointResponse(BaseModel):
    queryId: str
    index: str
    lng: float
    lat: float
    value: float | None
    masked: bool
    maskClass: int | None
    source: str


FieldIndexResponse = FieldIndexAvailableResponse | FieldIndexUnavailableResponse


class ReadinessUnavailableReason(BaseModel):
    code: str
    message: str


class ReadinessIndexCoverage(BaseModel):
    available: bool
    dateCount: int
    coveragePercent: float


class ReadinessLastSuccessfulJob(BaseModel):
    jobId: str
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    completedAt: str


class AnalyticsReadinessResponse(BaseModel):
    status: Literal["AVAILABLE", "STALE", "UNAVAILABLE"]
    sourceId: str
    aoiId: str
    providerRoute: str
    latestProcessedSceneDate: date | None
    latestSuccessfulJobCompletedAt: str | None
    lastSuccessfulJobAt: str | None
    staleAfter: str | None
    freshnessMaxAgeHours: int
    availableDates: list[date]
    indexCoverage: dict[str, ReadinessIndexCoverage]
    lastSuccessfulJob: ReadinessLastSuccessfulJob | None
    unavailableReasons: list[ReadinessUnavailableReason] = Field(default_factory=list)
    reasonCode: str | None = None
    reason: str | None = None
