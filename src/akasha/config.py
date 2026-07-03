from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    field_max_vertices: int = Field(default=5000, gt=0)
    field_max_area_sq_km: float = Field(default=25.0, gt=0)
    field_min_usable_pixels: int = Field(default=1, gt=0)
    max_candidate_scenes: int = Field(default=20, gt=0)
    backfill_search_item_cap: int = Field(default=1000, gt=0)
    field_usable_pixel_threshold: float = Field(default=0.80, ge=0, le=1)
    field_max_cloud_percentage: float = Field(default=20.0, ge=0, le=100)

    sentinel2_profile_version: str = "sentinel2-l2a-earthsearch-v1"
    selection_policy_version: str = "field-selection-v1"
    live_provider_tests: bool = False

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("aws_request_payer")
    @classmethod
    def normalize_aws_request_payer(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value.lower()


@lru_cache
def get_settings() -> Settings:
    return Settings()
