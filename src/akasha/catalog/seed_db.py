from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from akasha.catalog.seed import SEED_SOURCES
from akasha.config import get_settings
from akasha.db.session import create_db_engine

SOURCE_METADATA: dict[str, dict[str, Any]] = {
    "sentinel-2-l2a": {
        "status": "manual_only",
        "execution_policy_ref": "earthsearch-default",
        "validation_profile": {
            "version": "phase2-sentinel2-validation-v1",
            "checks": [
                "stac_asset_validation",
                "source_cog_mirror_validation",
                "scl_mask_validation",
                "field_index_response_validation",
            ],
        },
        "processing_profile": {
            "version": "sentinel2-l2a-earthsearch-v1",
            "source": "earthsearch",
            "level": "L2A",
            "mask": "scl-v1",
        },
        "license_profile": {
            "profile": "earthsearch-public-open-data",
            "serving": "internal_private",
        },
        "provider_metadata": {
            "primary_route": "earthsearch:sentinel-2-l2a",
            "pgstac_collection": "akasha-sentinel-2-l2a-derived-v1",
        },
    }
}

PROVIDER_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "source_id": "sentinel-2-l2a",
        "provider_adapter": "earthsearch",
        "provider_collection": "sentinel-2-l2a",
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "public_https",
        "execution_policy_ref": "earthsearch-default",
        "license_profile": "earthsearch-public-open-data",
        "metadata": {"route_key": "earthsearch:sentinel-2-l2a"},
    },
    {
        "source_id": "sentinel-2-l2a",
        "provider_adapter": "cdse",
        "provider_collection": "sentinel-2-l2a",
        "provider_priority": 50,
        "provider_role": "fallback",
        "status": "inactive",
        "access_mode": "authenticated_download",
        "execution_policy_ref": "cdse-default",
        "license_profile": "copernicus-official-api",
        "metadata": {"route_key": "cdse:sentinel-2-l2a", "phase2_critical_path": False},
    },
    {
        "source_id": "landsat-c2-l2",
        "provider_adapter": "earthsearch",
        "provider_collection": "landsat-c2-l2",
        "provider_priority": 20,
        "provider_role": "secondary",
        "status": "inactive",
        "access_mode": "public_https",
        "execution_policy_ref": "earthsearch-default",
        "license_profile": "usgs-open-data",
        "metadata": {
            "route_key": "earthsearch:landsat-c2-l2",
            "requester_pays_may_apply": True,
        },
    },
    {
        "source_id": "landsat-c2-l2",
        "provider_adapter": "usgs",
        "provider_collection": "landsat-c2-l2",
        "provider_priority": 60,
        "provider_role": "fallback",
        "status": "inactive",
        "access_mode": "official_api",
        "execution_policy_ref": "usgs-default",
        "license_profile": "usgs-official-api",
        "metadata": {"route_key": "usgs:landsat-c2-l2"},
    },
    {
        "source_id": "sentinel-1-grd",
        "provider_adapter": "earthsearch",
        "provider_collection": "sentinel-1-grd",
        "provider_priority": 90,
        "provider_role": "future",
        "status": "inactive",
        "access_mode": "public_https",
        "execution_policy_ref": "earthsearch-default",
        "license_profile": "earthsearch-public-open-data",
        "metadata": {
            "route_key": "earthsearch:sentinel-1-grd",
            "phase2_optical_fallback": False,
        },
    },
)

VISUALIZATION_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "index_name": "ndvi",
        "value_domain_min": -1,
        "value_domain_max": 1,
        "display_min": -0.2,
        "display_max": 0.9,
        "palette_json": [
            {"value": -0.2, "color": "#8c510a"},
            {"value": 0.2, "color": "#f6e8c3"},
            {"value": 0.5, "color": "#80cdc1"},
            {"value": 0.9, "color": "#01665e"},
        ],
        "nodata_color": "transparent",
        "version": "ndvi-default-v1",
        "is_default": True,
    },
)

THRESHOLD_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "profile_key": "ndvi-generic-v1",
        "index_name": "ndvi",
        "crop": None,
        "season": None,
        "aoi_id": None,
        "source_id": "sentinel-2-l2a",
        "classes_json": [
            {"label": "Bare or stressed", "min": -1.0, "max": 0.2},
            {"label": "Moderate vegetation", "min": 0.2, "max": 0.5},
            {"label": "Healthy crop", "min": 0.5, "max": 0.75},
            {"label": "Dense vegetation", "min": 0.75, "max": 1.0},
        ],
        "is_default": True,
        "version": "ndvi-generic-v1",
    },
)


def seed_sources(connection) -> None:
    for source in SEED_SOURCES:
        metadata = SOURCE_METADATA.get(source.source_id, {})
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
                    status,
                    execution_policy_ref,
                    validation_profile,
                    processing_profile,
                    license_profile,
                    provider_metadata
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
                    :status,
                    :execution_policy_ref,
                    CAST(:validation_profile AS jsonb),
                    CAST(:processing_profile AS jsonb),
                    CAST(:license_profile AS jsonb),
                    CAST(:provider_metadata AS jsonb)
                )
                ON CONFLICT (source_id) DO UPDATE
                SET catalog_slug = EXCLUDED.catalog_slug,
                    provider_adapter = EXCLUDED.provider_adapter,
                    instrument_mode = EXCLUDED.instrument_mode,
                    analysis_level = EXCLUDED.analysis_level,
                    supported_indices = EXCLUDED.supported_indices,
                    schedule_state = EXCLUDED.schedule_state,
                    product_exposure = EXCLUDED.product_exposure,
                    status = EXCLUDED.status,
                    execution_policy_ref = EXCLUDED.execution_policy_ref,
                    validation_profile = EXCLUDED.validation_profile,
                    processing_profile = EXCLUDED.processing_profile,
                    license_profile = EXCLUDED.license_profile,
                    provider_metadata = EXCLUDED.provider_metadata,
                    updated_at = now()
                """
            ),
            {
                **source.model_dump(),
                "supported_indices": json.dumps(source.supported_indices),
                "status": metadata.get("status", "disabled"),
                "execution_policy_ref": metadata.get("execution_policy_ref"),
                "validation_profile": json.dumps(metadata.get("validation_profile", {})),
                "processing_profile": json.dumps(metadata.get("processing_profile", {})),
                "license_profile": json.dumps(metadata.get("license_profile", {})),
                "provider_metadata": json.dumps(metadata.get("provider_metadata", {})),
            },
        )


def seed_execution_policies(connection) -> None:
    policies = [
        {
            "policy_key": "mock-default",
            "provider_adapter": "mock",
            "source_id": None,
            "auth_model": "none",
            "enabled": True,
            "version": "phase1-v1",
        },
        {
            "policy_key": "earthsearch-default",
            "provider_adapter": "earthsearch",
            "source_id": None,
            "auth_model": "none",
            "enabled": True,
            "version": "phase2-earthsearch-v1",
        },
        {
            "policy_key": "cdse-default",
            "provider_adapter": "cdse",
            "source_id": None,
            "auth_model": "oauth2",
            "enabled": False,
            "version": "phase1-v1",
        },
        {
            "policy_key": "bhoonidhi-default",
            "provider_adapter": "bhoonidhi",
            "source_id": None,
            "auth_model": "session_cookie_or_api_key",
            "enabled": False,
            "version": "phase1-v1",
        },
        {
            "policy_key": "usgs-default",
            "provider_adapter": "usgs",
            "source_id": None,
            "auth_model": "api_token",
            "enabled": False,
            "version": "phase1-v1",
        },
        {
            "policy_key": "earthdata-default",
            "provider_adapter": "earthdata",
            "source_id": None,
            "auth_model": "basic_or_token",
            "enabled": False,
            "version": "phase1-v1",
        },
    ]
    for policy in policies:
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
                    staging_policy_json,
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
                    '{"max_concurrent_searches": 1, "max_concurrent_source_mirrors": 1}'::jsonb,
                    '{"required": true, "algorithm": "sha256"}'::jsonb,
                    :version
                )
                ON CONFLICT (policy_key) DO UPDATE
                SET auth_model = EXCLUDED.auth_model,
                    enabled = EXCLUDED.enabled,
                    retry_policy_json = EXCLUDED.retry_policy_json,
                    staging_policy_json = EXCLUDED.staging_policy_json,
                    checksum_policy_json = EXCLUDED.checksum_policy_json,
                    version = EXCLUDED.version,
                    updated_at = now()
                """
            ),
            policy,
        )


def seed_provider_routes(connection) -> None:
    for route in PROVIDER_ROUTES:
        connection.execute(
            text(
                """
                INSERT INTO akasha.source_provider_routes (
                    source_id,
                    provider_adapter,
                    provider_collection,
                    provider_priority,
                    provider_role,
                    status,
                    access_mode,
                    execution_policy_ref,
                    license_profile,
                    metadata
                )
                VALUES (
                    :source_id,
                    :provider_adapter,
                    :provider_collection,
                    :provider_priority,
                    :provider_role,
                    :status,
                    :access_mode,
                    :execution_policy_ref,
                    :license_profile,
                    CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, provider_adapter, provider_collection) DO UPDATE
                SET provider_priority = EXCLUDED.provider_priority,
                    provider_role = EXCLUDED.provider_role,
                    status = EXCLUDED.status,
                    access_mode = EXCLUDED.access_mode,
                    execution_policy_ref = EXCLUDED.execution_policy_ref,
                    license_profile = EXCLUDED.license_profile,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """
            ),
            {**route, "metadata": json.dumps(route["metadata"])},
        )


def seed_profiles(connection) -> None:
    for profile in VISUALIZATION_PROFILES:
        connection.execute(
            text(
                """
                INSERT INTO akasha.visualization_profiles (
                    index_name,
                    value_domain_min,
                    value_domain_max,
                    display_min,
                    display_max,
                    palette_json,
                    nodata_color,
                    version,
                    is_default
                )
                VALUES (
                    :index_name,
                    :value_domain_min,
                    :value_domain_max,
                    :display_min,
                    :display_max,
                    CAST(:palette_json AS jsonb),
                    :nodata_color,
                    :version,
                    :is_default
                )
                ON CONFLICT (index_name, version) DO UPDATE
                SET value_domain_min = EXCLUDED.value_domain_min,
                    value_domain_max = EXCLUDED.value_domain_max,
                    display_min = EXCLUDED.display_min,
                    display_max = EXCLUDED.display_max,
                    palette_json = EXCLUDED.palette_json,
                    nodata_color = EXCLUDED.nodata_color,
                    is_default = EXCLUDED.is_default,
                    updated_at = now()
                """
            ),
            {**profile, "palette_json": json.dumps(profile["palette_json"])},
        )

    for profile in THRESHOLD_PROFILES:
        connection.execute(
            text(
                """
                INSERT INTO akasha.threshold_profiles (
                    profile_key,
                    index_name,
                    crop,
                    season,
                    aoi_id,
                    source_id,
                    classes_json,
                    is_default,
                    version
                )
                VALUES (
                    :profile_key,
                    :index_name,
                    :crop,
                    :season,
                    :aoi_id,
                    :source_id,
                    CAST(:classes_json AS jsonb),
                    :is_default,
                    :version
                )
                ON CONFLICT (profile_key) DO UPDATE
                SET index_name = EXCLUDED.index_name,
                    crop = EXCLUDED.crop,
                    season = EXCLUDED.season,
                    aoi_id = EXCLUDED.aoi_id,
                    source_id = EXCLUDED.source_id,
                    classes_json = EXCLUDED.classes_json,
                    is_default = EXCLUDED.is_default,
                    version = EXCLUDED.version,
                    updated_at = now()
                """
            ),
            {**profile, "classes_json": json.dumps(profile["classes_json"])},
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
        seed_provider_routes(connection)
        seed_profiles(connection)
        seed_aoi(connection, aoi_geojson_path)


if __name__ == "__main__":
    main()
