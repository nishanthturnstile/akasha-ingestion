from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from akasha.catalog.seed import SEED_SOURCES
from akasha.config import get_settings
from akasha.db.session import create_db_engine


def seed_sources(connection) -> None:
    for source in SEED_SOURCES:
        connection.execute(
            text(
                """
                INSERT INTO akasha.satellite_sources (
                    source_id,
                    catalog_slug,
                    provider_adapter,
                    instrument_mode,
                    analysis_level,
                    supported_indices,
                    schedule_state,
                    product_exposure,
                    status
                )
                VALUES (
                    :source_id,
                    :catalog_slug,
                    :provider_adapter,
                    :instrument_mode,
                    :analysis_level,
                    CAST(:supported_indices AS jsonb),
                    :schedule_state,
                    :product_exposure,
                    'disabled'
                )
                ON CONFLICT (source_id) DO UPDATE
                SET supported_indices = EXCLUDED.supported_indices,
                    updated_at = now()
                """
            ),
            {
                **source.model_dump(),
                "supported_indices": json.dumps(source.supported_indices),
            },
        )


def seed_execution_policies(connection) -> None:
    policies = [
        ("mock-default", "mock", None, "none", True),
        ("cdse-default", "cdse", None, "oauth2", False),
        ("bhoonidhi-default", "bhoonidhi", None, "session_cookie_or_api_key", False),
        ("usgs-default", "usgs", None, "api_token", False),
        ("earthdata-default", "earthdata", None, "basic_or_token", False),
    ]
    for policy_key, provider_adapter, source_id, auth_model, enabled in policies:
        connection.execute(
            text(
                """
                INSERT INTO akasha.provider_execution_policies (
                    policy_key,
                    provider_adapter,
                    source_id,
                    auth_model,
                    enabled,
                    retry_policy_json,
                    checksum_policy_json,
                    version
                )
                VALUES (
                    :policy_key,
                    :provider_adapter,
                    :source_id,
                    :auth_model,
                    :enabled,
                    '{"max_attempts": 3, "backoff": "exponential", "jitter": true}'::jsonb,
                    '{"required": true, "algorithm": "sha256"}'::jsonb,
                    'phase1-v1'
                )
                ON CONFLICT (policy_key) DO UPDATE
                SET auth_model = EXCLUDED.auth_model,
                    enabled = EXCLUDED.enabled,
                    updated_at = now()
                """
            ),
            {
                "policy_key": policy_key,
                "provider_adapter": provider_adapter,
                "source_id": source_id,
                "auth_model": auth_model,
                "enabled": enabled,
            },
        )


def seed_aoi(connection, aoi_geojson_path: Path) -> None:
    geojson = json.loads(aoi_geojson_path.read_text(encoding="utf-8"))
    global_bbox = geojson.get("bbox", [])
    for feature in geojson["features"]:
        role = feature.get("properties", {}).get("role")
        if role not in {"aoi_polygon", "sample_field"}:
            continue
        feature_id = feature["id"]
        connection.execute(
            text(
                """
                INSERT INTO akasha.aoi_registry (aoi_id, name, geometry, bbox, metadata)
                VALUES (
                    :aoi_id,
                    :name,
                    ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326),
                    CAST(:bbox AS jsonb),
                    CAST(:metadata AS jsonb)
                )
                ON CONFLICT (aoi_id) DO UPDATE
                SET geometry = EXCLUDED.geometry,
                    bbox = EXCLUDED.bbox,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """
            ),
            {
                "aoi_id": feature_id,
                "name": feature_id.replace("_", " ").title(),
                "geometry": json.dumps(feature["geometry"]),
                "bbox": json.dumps(feature.get("bbox", global_bbox)),
                "metadata": json.dumps(feature.get("properties", {})),
            },
        )


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def main() -> None:
    settings = get_settings()
    engine = create_db_engine(settings)
    aoi_geojson_path = _resolve_path(settings.aoi_geojson_path)
    with engine.begin() as connection:
        seed_sources(connection)
        seed_execution_policies(connection)
        seed_aoi(connection, aoi_geojson_path)


if __name__ == "__main__":
    main()
