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


def compute_backfill_idempotency_key(
    *,
    source_id: str,
    provider_route: str,
    aoi_id: str,
    date_start: str,
    date_end: str,
    mode: str,
    request_params_version: str,
    processing_profile_version: str,
) -> str:
    return _hash_material(
        {
            "job_type": "sentinel2_backfill",
            "source_id": source_id,
            "provider_route": provider_route,
            "aoi_id": aoi_id,
            "date_start": date_start,
            "date_end": date_end,
            "mode": mode,
            "request_params_version": request_params_version,
            "processing_profile_version": processing_profile_version,
        }
    )


def compute_scene_idempotency_key(
    *,
    source_id: str,
    provider_route: str,
    aoi_id: str,
    stac_item_id: str,
    logical_scene_key: str,
    request_params_version: str,
    processing_profile_version: str,
) -> str:
    return _hash_material(
        {
            "job_type": "sentinel2_scene",
            "source_id": source_id,
            "provider_route": provider_route,
            "aoi_id": aoi_id,
            "stac_item_id": stac_item_id,
            "logical_scene_key": logical_scene_key,
            "request_params_version": request_params_version,
            "processing_profile_version": processing_profile_version,
        }
    )


def compute_asset_mirror_idempotency_key(
    *,
    source_id: str,
    provider_route: str,
    stac_item_id: str,
    asset_key: str,
    request_params_version: str,
    processing_profile_version: str,
) -> str:
    return _hash_material(
        {
            "job_type": "source_asset_mirror",
            "source_id": source_id,
            "provider_route": provider_route,
            "stac_item_id": stac_item_id,
            "asset_key": asset_key,
            "request_params_version": request_params_version,
            "processing_profile_version": processing_profile_version,
        }
    )


def compute_index_output_idempotency_key(
    *,
    source_id: str,
    provider_route: str,
    stac_item_id: str,
    index_name: str,
    formula_version: str,
    request_params_version: str,
    processing_profile_version: str,
) -> str:
    return _hash_material(
        {
            "job_type": "sentinel2_index_output",
            "source_id": source_id,
            "provider_route": provider_route,
            "stac_item_id": stac_item_id,
            "index_name": index_name,
            "formula_version": formula_version,
            "request_params_version": request_params_version,
            "processing_profile_version": processing_profile_version,
        }
    )


def _hash_material(material: dict[str, str]) -> str:
    return sha256(
        dumps(material, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
