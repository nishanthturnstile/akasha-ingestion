from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from akasha.config import Environment
from akasha.jobs.store import Job

T = TypeVar("T")


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


class SyncRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1)
    aoi_id: str = Field(min_length=1)
    date_start: date
    date_end: date
    job_type: Literal["mock_sync"] = "mock_sync"

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.date_end < self.date_start:
            raise ValueError("date_end must be on or after date_start")
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
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
