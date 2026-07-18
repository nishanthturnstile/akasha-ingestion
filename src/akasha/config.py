from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"
    TEST = "test"


class RuntimeBackend(StrEnum):
    EXTERNAL = "external"
    MEMORY = "memory"


class SourceMirrorMode(StrEnum):
    AOI_CLIPPED = "aoi_clipped"
    FULL_ASSET = "full_asset"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AKASHA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    runtime_backend: RuntimeBackend = RuntimeBackend.EXTERNAL
    task_always_eager: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://akasha:akasha@postgres:5432/akasha"
    redis_url: str = "redis://redis:6379/0"

    minio_endpoint: str = "minio:9000"
    minio_bucket: str = "akasha-data"
    minio_access_key: str = "akasha"
    minio_secret_key: SecretStr = Field(default=SecretStr("change-me"))
    minio_secure: bool = False
    s3_endpoint_url: str = "http://minio:9000"

    api_key_hashes: str = ""
    public_base_url: str = "http://localhost:8080"

    raw_lifecycle_cleanup_enabled: bool = False
    request_params_version: str = "v1"
    processing_profile_version: str = "phase1-mock-v1"
    aoi_geojson_path: Path = Path("docs/phase-0/bangalore-aoi.geojson")

    earthsearch_api_url: str = "https://earth-search.aws.element84.com/v1"
    earthsearch_timeout_seconds: float = Field(default=30.0, gt=0)
    earthsearch_page_size: int = Field(default=100, gt=0, le=1000)

    titiler_internal_url: str = "http://titiler:8000"
    titiler_timeout_seconds: float = Field(default=30.0, gt=0)

    source_mirror_mode: SourceMirrorMode = SourceMirrorMode.AOI_CLIPPED
    source_mirror_max_bytes_per_run: int | None = Field(default=None, gt=0)
    source_mirror_required_headroom_bytes: int = Field(default=0, ge=0)

    enable_landsat_requester_pays: bool = False
    aws_request_payer: str | None = None
    aws_region: str = "us-west-2"

    signing_secret: SecretStr = Field(default=SecretStr("change-me"))
    signed_url_ttl_seconds: int = Field(default=900, gt=0)

    scratch_dir: Path = Path("/tmp/akasha")
    gdal_cachemax_mb: int = Field(default=512, gt=0)

    bhoonidhi_api_base: str = "https://bhoonidhi-api.nrsc.gov.in"
    bhoonidhi_user_id: str = ""
    bhoonidhi_password: SecretStr = Field(default=SecretStr(""))
    bhoonidhi_search_rps: float = Field(default=0.5, gt=0)
    bhoonidhi_timeout_seconds: float = Field(default=300.0, gt=0)
    bhoonidhi_download_chunk_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    bhoonidhi_max_downloads_per_run: int = Field(default=1, gt=0)
    bhoonidhi_approved_runtime_required: bool = True
    bhoonidhi_approved_runtime: bool = False

    resourcesat_profile_version: str = "resourcesat-phase3-v1"
    resourcesat_liss3_profile_version: str = "resourcesat-liss3-boa-v1"
    resourcesat_liss4_profile_version: str = "resourcesat-liss4-mx70-l2-v1"
    resourcesat_awifs_profile_version: str = "resourcesat-awifs-boa-v1"
    resourcesat_backfill_date_window_days: int = Field(default=30, gt=0)
    resourcesat_approved_data_root: Path | None = None

    resourcesat_liss3_preload_source_id: str = "resourcesat-2a-liss3-boa"
    resourcesat_liss3_preload_aoi_id: str = "bangalore_60km_geodesic_aoi"
    resourcesat_liss3_preload_provider_route: str = "bhoonidhi:ResourceSat-2A_LISS3_BOA"
    resourcesat_liss3_preload_date_window_days: int = Field(default=30, gt=0)
    resourcesat_liss3_preload_refresh_days: int = Field(default=14, gt=0)
    resourcesat_liss3_preload_freshness_max_age_hours: int = Field(default=336, gt=0)
    resourcesat_liss3_max_downloads_per_run: int = Field(default=1, gt=0)
    resourcesat_liss3_preload_schedule_enabled: bool = False
    resourcesat_liss3_preload_schedule_day_of_week: str = "tue"
    resourcesat_liss3_preload_schedule_hour_utc: int = Field(default=3, ge=0, le=23)
    resourcesat_liss3_preload_schedule_minute_utc: int = Field(default=0, ge=0, le=59)
    resourcesat_liss3_readiness_enabled: bool = False
    resourcesat_liss3_readiness_required_indices: Annotated[tuple[str, ...], NoDecode] = (
        "ndvi",
    )
    resourcesat_liss3_processing_resolution_m: float | None = Field(default=None, gt=0)
    resourcesat_liss3_composite_min_coverage_percent: float = Field(
        default=95.0,
        ge=0,
        le=100,
    )

    resourcesat_liss4_preload_source_id: str = "resourcesat-2a-liss4-mx70-l2"
    resourcesat_liss4_preload_aoi_id: str = "bangalore_60km_geodesic_aoi"
    resourcesat_liss4_preload_provider_route: str = "bhoonidhi:ResourceSat-2A_LISS4-MX70_L2"
    resourcesat_liss4_preload_date_window_days: int = Field(default=365, gt=0)
    resourcesat_liss4_preload_refresh_days: int = Field(default=14, gt=0)
    resourcesat_liss4_preload_freshness_max_age_hours: int = Field(default=336, gt=0)
    resourcesat_liss4_max_downloads_per_run: int = Field(default=1, gt=0)
    resourcesat_liss4_preload_schedule_enabled: bool = False
    resourcesat_liss4_preload_schedule_day_of_week: str = "wed"
    resourcesat_liss4_preload_schedule_hour_utc: int = Field(default=3, ge=0, le=23)
    resourcesat_liss4_preload_schedule_minute_utc: int = Field(default=30, ge=0, le=59)
    resourcesat_liss4_readiness_enabled: bool = False
    resourcesat_liss4_readiness_required_indices: Annotated[tuple[str, ...], NoDecode] = (
        "ndvi",
    )
    resourcesat_liss4_processing_resolution_m: float | None = Field(default=None, gt=0)
    resourcesat_liss4_composite_min_coverage_percent: float = Field(
        default=10.0,
        ge=0,
        le=100,
    )

    resourcesat_awifs_preload_source_id: str = "resourcesat-2a-awifs-boa"
    resourcesat_awifs_preload_aoi_id: str = "bangalore_60km_geodesic_aoi"
    resourcesat_awifs_preload_provider_route: str = "bhoonidhi:ResourceSat-2A_AWIFS_BOA"
    resourcesat_awifs_preload_date_window_days: int = Field(default=365, gt=0)
    resourcesat_awifs_preload_refresh_days: int = Field(default=14, gt=0)
    resourcesat_awifs_preload_freshness_max_age_hours: int = Field(default=336, gt=0)
    resourcesat_awifs_max_downloads_per_run: int = Field(default=1, gt=0)
    resourcesat_awifs_preload_schedule_enabled: bool = False
    resourcesat_awifs_preload_schedule_day_of_week: str = "thu"
    resourcesat_awifs_preload_schedule_hour_utc: int = Field(default=4, ge=0, le=23)
    resourcesat_awifs_preload_schedule_minute_utc: int = Field(default=0, ge=0, le=59)
    resourcesat_awifs_readiness_enabled: bool = False
    resourcesat_awifs_readiness_required_indices: Annotated[tuple[str, ...], NoDecode] = (
        "ndvi",
    )
    resourcesat_awifs_processing_resolution_m: float | None = Field(default=None, gt=0)
    resourcesat_awifs_composite_min_coverage_percent: float = Field(
        default=60.0,
        ge=0,
        le=100,
    )

    eos04_profile_version: str = "eos04-sar-mrs-l2b-gamma0-v2"
    eos04_preload_source_id: str = "eos-04-sar-mrs-l2b"
    eos04_preload_aoi_id: str = "bangalore_60km_geodesic_aoi"
    eos04_preload_provider_route: str = "bhoonidhi:EOS-04_SAR-MRS_L2B"
    eos04_preload_date_window_days: int = Field(default=365, gt=0)
    eos04_preload_refresh_days: int = Field(default=12, gt=0)
    eos04_max_downloads_per_run: int = Field(default=1, gt=0, le=1)
    eos04_preload_schedule_enabled: bool = False
    eos04_preload_schedule_hour_utc: int = Field(default=5, ge=0, le=23)
    eos04_preload_schedule_minute_utc: int = Field(default=0, ge=0, le=59)

    field_max_vertices: int = Field(default=5000, gt=0)
    field_max_area_sq_km: float = Field(default=25.0, gt=0)
    field_min_usable_pixels: int = Field(default=1, gt=0)
    max_candidate_scenes: int = Field(default=20, gt=0)
    field_query_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    backfill_search_item_cap: int = Field(default=1000, gt=0)
    field_usable_pixel_threshold: float = Field(default=0.80, ge=0, le=1)
    field_max_cloud_percentage: float = Field(default=20.0, ge=0, le=20)
    field_min_coverage_percentage: float = Field(default=95.0, ge=0, le=100)

    sentinel2_profile_version: str = "sentinel2-l2a-earthsearch-v1"
    selection_policy_version: str = "field-selection-v1"
    sentinel2_preload_source_id: str = "sentinel-2-l2a"
    sentinel2_preload_aoi_id: str = "bangalore_60km_geodesic_aoi"
    sentinel2_preload_provider_route: str = "earthsearch:sentinel-2-l2a"
    sentinel2_preload_mode: Literal["metadata_only", "mirror_only", "full_pipeline"] = (
        "full_pipeline"
    )
    sentinel2_preload_date_window_days: int = Field(default=180, gt=0)
    sentinel2_preload_refresh_days: int = Field(default=7, gt=0)
    sentinel2_revisit_days: int = Field(default=5, gt=0)
    sentinel2_preload_freshness_max_age_hours: int = Field(default=168, gt=0)
    sentinel2_preload_schedule_enabled: bool = True
    # Deprecated compatibility input. Daily discovery now uses only the configured UTC time.
    sentinel2_preload_schedule_day_of_week: str = "mon"
    sentinel2_preload_schedule_hour_utc: int = Field(default=2, ge=0, le=23)
    sentinel2_preload_schedule_minute_utc: int = Field(default=30, ge=0, le=59)
    live_provider_tests: bool = False

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("aws_request_payer", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("aws_request_payer")
    @classmethod
    def normalize_aws_request_payer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.lower()

    @field_validator("source_mirror_max_bytes_per_run", mode="before")
    @classmethod
    def normalize_optional_int(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "resourcesat_liss3_readiness_required_indices",
        "resourcesat_liss4_readiness_required_indices",
        "resourcesat_awifs_readiness_required_indices",
        mode="before",
    )
    @classmethod
    def normalize_index_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        return value

    @field_validator(
        "resourcesat_liss3_processing_resolution_m",
        "resourcesat_liss4_processing_resolution_m",
        "resourcesat_awifs_processing_resolution_m",
        mode="before",
    )
    @classmethod
    def normalize_optional_float(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("resourcesat_approved_data_root", mode="before")
    @classmethod
    def normalize_optional_path(cls, value: object) -> object:
        if value == "":
            return None
        return value

    def resourcesat_max_downloads_for_source(self, source_id: str) -> int:
        """Return the memory-safe scene cap configured for a ResourceSat source."""
        caps = {
            self.resourcesat_liss3_preload_source_id: self.resourcesat_liss3_max_downloads_per_run,
            self.resourcesat_liss4_preload_source_id: self.resourcesat_liss4_max_downloads_per_run,
            self.resourcesat_awifs_preload_source_id: self.resourcesat_awifs_max_downloads_per_run,
        }
        return caps.get(source_id, self.bhoonidhi_max_downloads_per_run)


_DEFAULT_RESOURCESAT_APPROVED_ROOT = Path("/srv/akasha")
_UNSAFE_RESOURCESAT_RUNTIME_ROOTS = (
    PurePosixPath("/"),
    PurePosixPath("/tmp"),
    PurePosixPath("/var/tmp"),
    PurePosixPath("/var/lib/docker"),
    PurePosixPath("/data/coolify"),
)


def validate_resourcesat_runtime_roots(settings: Settings, *, dry_run: bool) -> None:
    if dry_run:
        return

    runtime_roots = {"scratch_dir": settings.scratch_dir}
    approved_roots = [_DEFAULT_RESOURCESAT_APPROVED_ROOT]
    if settings.resourcesat_approved_data_root is not None:
        approved_roots.append(settings.resourcesat_approved_data_root)

    unsafe_approved = [
        str(root) for root in approved_roots if _is_unsafe_resourcesat_root(Path(root))
    ]
    if unsafe_approved:
        joined = ", ".join(unsafe_approved)
        raise ValueError(f"unsafe ResourceSat approved data root configured: {joined}")

    for name, root in runtime_roots.items():
        path = Path(root)
        if _is_unsafe_resourcesat_root(path):
            raise ValueError(f"unsafe ResourceSat runtime root for {name}: {path}")
        if not any(_is_under_approved_root(path, approved) for approved in approved_roots):
            approved = ", ".join(str(item) for item in approved_roots)
            raise ValueError(
                f"ResourceSat runtime root for {name} must be under an approved data root "
                f"({approved}): {path}"
            )


def _is_unsafe_resourcesat_root(path: Path) -> bool:
    return any(_is_unsafe_posix_path(item) for item in _path_posix_forms(path))


def _path_posix_forms(path: Path) -> tuple[str, str]:
    resolved = path.expanduser().resolve(strict=False)
    return (path.as_posix(), resolved.as_posix())


def _is_unsafe_posix_path(path_text: str) -> bool:
    path = PurePosixPath(path_text)
    for unsafe in _UNSAFE_RESOURCESAT_RUNTIME_ROOTS:
        if unsafe == PurePosixPath("/"):
            if path == unsafe:
                return True
            continue
        if _is_relative_to_posix(path, unsafe):
            return True
    return False


def _is_under_approved_root(path: Path, approved_root: Path) -> bool:
    if _is_relative_to_path(
        path.expanduser().resolve(strict=False),
        approved_root.expanduser().resolve(strict=False),
    ):
        return True
    return _is_relative_to_posix(
        PurePosixPath(path.as_posix()),
        PurePosixPath(approved_root.as_posix()),
    )


def _is_relative_to_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_relative_to_posix(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
