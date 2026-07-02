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

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
