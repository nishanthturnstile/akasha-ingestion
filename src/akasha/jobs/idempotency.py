from __future__ import annotations

from hashlib import sha256
from json import dumps


def compute_sync_idempotency_key(
    *,
    source_id: str,
    aoi_id: str,
    date_start: str,
    date_end: str,
    job_type: str,
    request_params_version: str,
    processing_profile_version: str,
) -> str:
    material = dumps(
        {
            "source_id": source_id,
            "aoi_id": aoi_id,
            "date_start": date_start,
            "date_end": date_end,
            "job_type": job_type,
            "request_params_version": request_params_version,
            "processing_profile_version": processing_profile_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(material.encode()).hexdigest()
