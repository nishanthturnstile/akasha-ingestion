from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from akasha.config import Settings
from akasha.processing.resourcesat import (
    AWIFS_PROFILE,
    LISS3_PROFILE,
    LISS4_PROFILE,
    ResourceSatProfile,
)

SourceLifecycleState = Literal["disabled", "manual", "scheduled"]
SourceScheduleState = Literal["disabled", "manual", "scheduled"]
SourceProductExposure = Literal["hidden", "admin", "public"]
SourceValidationState = Literal["pending", "accepted"]
SourceCadenceClass = Literal[
    "revisit_5d",
    "revisit_12d",
    "revisit_24d",
    "regional_context",
]


@dataclass(frozen=True, slots=True)
class SourceAoiState:
    source_id: str
    aoi_id: str
    provider_route: str
    date_window_days: int
    refresh_days: int
    freshness_max_age_hours: int
    max_downloads: int
    min_coverage_percent: float
    composite_window_days: int


@dataclass(frozen=True, slots=True)
class SourceState:
    source_id: str
    provider_route: str
    lifecycle_state: SourceLifecycleState
    schedule_state: SourceScheduleState
    capabilities: tuple[str, ...]
    product_exposure: SourceProductExposure
    commercial_state: Literal["restricted"]
    aoi_scope: Literal["configured_aois"]
    validation_state: SourceValidationState
    readiness_reasons: tuple[str, ...]
    validation_profile: str
    cadence_class: SourceCadenceClass
    host_pool: Literal["akasha-staging"]
    owner: Literal["akasha-ingestion"]
    default_aois: tuple[SourceAoiState, ...]


def resourcesat_source_registry(settings: Settings) -> tuple[SourceState, ...]:
    return (
        _state(
            profile=LISS3_PROFILE,
            settings=settings,
            aoi_id=settings.resourcesat_liss3_preload_aoi_id,
            provider_route=settings.resourcesat_liss3_preload_provider_route,
            date_window_days=settings.resourcesat_liss3_preload_date_window_days,
            refresh_days=settings.resourcesat_liss3_preload_refresh_days,
            freshness_max_age_hours=settings.resourcesat_liss3_preload_freshness_max_age_hours,
            max_downloads=settings.resourcesat_liss3_max_downloads_per_run,
            schedule_enabled=settings.resourcesat_liss3_preload_schedule_enabled,
            min_coverage_percent=settings.resourcesat_liss3_composite_min_coverage_percent,
            cadence_class="revisit_24d",
        ),
        _state(
            profile=LISS4_PROFILE,
            settings=settings,
            aoi_id=settings.resourcesat_liss4_preload_aoi_id,
            provider_route=settings.resourcesat_liss4_preload_provider_route,
            date_window_days=settings.resourcesat_liss4_preload_date_window_days,
            refresh_days=settings.resourcesat_liss4_preload_refresh_days,
            freshness_max_age_hours=settings.resourcesat_liss4_preload_freshness_max_age_hours,
            max_downloads=settings.resourcesat_liss4_max_downloads_per_run,
            schedule_enabled=settings.resourcesat_liss4_preload_schedule_enabled,
            min_coverage_percent=settings.resourcesat_liss4_composite_min_coverage_percent,
            cadence_class="revisit_5d",
        ),
        _state(
            profile=AWIFS_PROFILE,
            settings=settings,
            aoi_id=settings.resourcesat_awifs_preload_aoi_id,
            provider_route=settings.resourcesat_awifs_preload_provider_route,
            date_window_days=settings.resourcesat_awifs_preload_date_window_days,
            refresh_days=settings.resourcesat_awifs_preload_refresh_days,
            freshness_max_age_hours=settings.resourcesat_awifs_preload_freshness_max_age_hours,
            max_downloads=settings.resourcesat_awifs_max_downloads_per_run,
            schedule_enabled=settings.resourcesat_awifs_preload_schedule_enabled,
            min_coverage_percent=settings.resourcesat_awifs_composite_min_coverage_percent,
            cadence_class="regional_context",
        ),
    )


def source_state_by_id(settings: Settings, source_id: str) -> SourceState | None:
    return next(
        (
            source
            for source in ingestion_source_registry(settings)
            if source.source_id == source_id
        ),
        None,
    )


def eos04_source_state(settings: Settings) -> SourceState:
    aoi = SourceAoiState(
        source_id=settings.eos04_preload_source_id,
        aoi_id=settings.eos04_preload_aoi_id,
        provider_route=settings.eos04_preload_provider_route,
        date_window_days=settings.eos04_preload_date_window_days,
        refresh_days=settings.eos04_preload_refresh_days,
        freshness_max_age_hours=settings.eos04_preload_refresh_days * 24,
        max_downloads=settings.eos04_max_downloads_per_run,
        min_coverage_percent=0.0,
        composite_window_days=0,
    )
    schedule_state: SourceScheduleState = (
        "scheduled" if settings.eos04_preload_schedule_enabled else "manual"
    )
    lifecycle_state: SourceLifecycleState = (
        "scheduled" if settings.eos04_preload_schedule_enabled else "manual"
    )
    return SourceState(
        source_id=settings.eos04_preload_source_id,
        provider_route=settings.eos04_preload_provider_route,
        lifecycle_state=lifecycle_state,
        schedule_state=schedule_state,
        capabilities=("search", "download", "prepare", "validate", "catalog", "tiles"),
        product_exposure="hidden",
        commercial_state="restricted",
        aoi_scope="configured_aois",
        validation_state="pending",
        readiness_reasons=(
            "EOS04_REAL_PRODUCT_NOT_VALIDATED",
            "EOS04_PRODUCT_EXPOSURE_DISABLED",
        ),
        validation_profile=settings.eos04_profile_version,
        cadence_class="revisit_12d",
        host_pool="akasha-staging",
        owner="akasha-ingestion",
        default_aois=(aoi,),
    )


def ingestion_source_registry(settings: Settings) -> tuple[SourceState, ...]:
    return (*resourcesat_source_registry(settings), eos04_source_state(settings))


def _state(
    *,
    profile: ResourceSatProfile,
    settings: Settings,
    aoi_id: str,
    provider_route: str,
    date_window_days: int,
    refresh_days: int,
    freshness_max_age_hours: int,
    max_downloads: int,
    schedule_enabled: bool,
    min_coverage_percent: float,
    cadence_class: SourceCadenceClass,
) -> SourceState:
    schedule_state: SourceScheduleState = "scheduled" if schedule_enabled else "manual"
    lifecycle_state: SourceLifecycleState = "scheduled" if schedule_enabled else "manual"
    aoi = SourceAoiState(
        source_id=profile.source_id,
        aoi_id=aoi_id,
        provider_route=provider_route,
        date_window_days=date_window_days,
        refresh_days=refresh_days,
        freshness_max_age_hours=freshness_max_age_hours,
        max_downloads=max_downloads,
        min_coverage_percent=min_coverage_percent,
        composite_window_days=date_window_days,
    )
    return SourceState(
        source_id=profile.source_id,
        provider_route=provider_route,
        lifecycle_state=lifecycle_state,
        schedule_state=schedule_state,
        capabilities=("search", "download", "prepare", "composite", "indices", "readiness"),
        product_exposure="public",
        commercial_state="restricted",
        aoi_scope="configured_aois",
        validation_state="accepted",
        readiness_reasons=(
            "SOURCE_NOT_ENABLED",
            "NO_SUCCESSFUL_RESOURCE_SAT_JOB",
            "NO_RESOURCE_SAT_OUTPUTS",
            "MISSING_INDEX_COVERAGE",
            "RESOURCE_SAT_STALE",
            "LOW_COVERAGE",
        ),
        validation_profile=profile.validation_profile_version,
        cadence_class=cadence_class,
        host_pool="akasha-staging",
        owner="akasha-ingestion",
        default_aois=(aoi,),
    )
