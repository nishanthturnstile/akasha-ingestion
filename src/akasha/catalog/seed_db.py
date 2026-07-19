from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from akasha.catalog.seed import SEED_SOURCES
from akasha.config import get_settings
from akasha.db.session import create_db_engine
from akasha.processing.eos04 import (
    EOS04_COLLECTION_ID,
    EOS04_PGSTAC_COLLECTION_ID,
    EOS04_PROCESSING_PROFILE_VERSION,
    EOS04_SOURCE_ID,
)
from akasha.processing.landsat import (
    LANDSAT_MASK_PROFILE_VERSION,
    LANDSAT_PGSTAC_COLLECTION_ID,
    LANDSAT_PRIMARY_PROVIDER_ROUTE,
    LANDSAT_PROCESSING_PROFILE_VERSION,
    LANDSAT_PROVIDER_COLLECTION,
    LANDSAT_REFLECTANCE_OFFSET,
    LANDSAT_REFLECTANCE_SCALE,
    LANDSAT_SOURCE_ID,
)
from akasha.processing.nisar import (
    NISAR_COLLECTION_ID,
    NISAR_PGSTAC_COLLECTION_ID,
    NISAR_PROCESSING_PROFILE_VERSION,
    NISAR_SOURCE_ID,
)
from akasha.processing.resourcesat import (
    BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
    BHOONIDHI_LISS3_BOA_COLLECTION_ID,
    BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    RESOURCESAT_MASK_METHOD,
    RESOURCESAT_PROFILES,
    RESOURCESAT_VALID_MASK_CLASSES,
)

DEFAULT_RETRY_POLICY = {"max_attempts": 3, "backoff": "exponential", "jitter": True}
DEFAULT_STAGING_POLICY = {"max_concurrent_searches": 1, "max_concurrent_source_mirrors": 1}
DEFAULT_CHECKSUM_POLICY = {"required": True, "algorithm": "sha256"}
BHOONIDHI_RETRY_POLICY = {
    "max_attempts": 3,
    "backoff": "exponential",
    "jitter": True,
    "retryable_errors": ["timeout", "rate_limited", "transient_provider_error"],
}
BHOONIDHI_STAGING_POLICY = {
    "enabled_by_default": False,
    "approved_runtime_required": True,
    "dry_run_allowed_without_credentials": True,
    "data_root_required": "/srv/akasha",
    "concurrency": {
        "max_concurrent_searches": 1,
        "max_concurrent_downloads": 1,
        "max_concurrent_processing_jobs": 1,
    },
    "redaction": {
        "enabled": True,
        "redact_before_persist": True,
        "fields": [
            "username",
            "password",
            "token",
            "cookie",
            "download_url",
            "signed_url",
            "provider_href",
        ],
    },
}
BHOONIDHI_CHECKSUM_POLICY = {
    "required": True,
    "algorithm": "sha256",
    "sidecar_required": True,
    "validate_on_download": True,
    "validate_before_catalog": True,
}

RESOURCESAT_SOURCE_PROFILES: dict[str, dict[str, Any]] = {
    source_id: {
        "instrument": profile.instrument,
        "analysis_level": profile.analysis_level,
        "provider_collection": profile.collection_id,
        "pgstac_collection": profile.pgstac_collection,
        "processing_profile_version": profile.processing_profile_version,
        "validation_profile_version": profile.validation_profile_version,
        "native_resolution_m": profile.native_resolution_m,
        "native_resolution_tolerance_m": profile.native_resolution_tolerance_m,
        "supported_indices": list(profile.supported_indices),
        "band_order": list(profile.band_order),
        "band_roles": dict(profile.band_roles),
        "source_notes": profile.source_notes,
    }
    for source_id, profile in RESOURCESAT_PROFILES.items()
}


def _resourcesat_source_metadata(source_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "disabled",
        "execution_policy_ref": "bhoonidhi-default",
        "validation_profile": {
            "version": profile["validation_profile_version"],
            "profile_family": "resourcesat-bhoonidhi-phase3",
            "checks": [
                "bhoonidhi_collection_contract",
                "raw_checksum_validation",
                "resourcesat_band_order_validation",
                "resourcesat_mask_validation",
                "derived_index_coverage_validation",
                "pgstac_collection_registration",
                "field_index_response_validation",
            ],
            "mask_method": RESOURCESAT_MASK_METHOD,
            "valid_mask_classes": list(RESOURCESAT_VALID_MASK_CLASSES),
            "notes": profile["source_notes"],
        },
        "processing_profile": {
            "version": profile["processing_profile_version"],
            "source": "bhoonidhi",
            "level": profile["analysis_level"],
            "instrument": profile["instrument"],
            "band_order": profile["band_order"],
            "band_roles": profile["band_roles"],
            "reflectance_scale": 0.0001,
            "reflectance_offset": 0.0,
            "mask_method": RESOURCESAT_MASK_METHOD,
            "valid_mask_classes": list(RESOURCESAT_VALID_MASK_CLASSES),
            "supported_indices": profile["supported_indices"],
            "native_resolution_m": profile["native_resolution_m"],
            "native_resolution_tolerance_m": profile["native_resolution_tolerance_m"],
            "display_composite": "FCC_NIR_RED_GREEN",
        },
        "license_profile": {
            "profile": "nrsc-bhoonidhi-restricted",
            "provider": "NRSC Bhoonidhi",
            "serving": "internal_private",
            "redistribution": "not_public",
            "credential_required": True,
        },
        "provider_metadata": {
            "primary_route": f"bhoonidhi:{profile['provider_collection']}",
            "provider_adapter": "bhoonidhi",
            "provider_collection": profile["provider_collection"],
            "pgstac_collection": profile["pgstac_collection"],
            "mask_method": RESOURCESAT_MASK_METHOD,
            "profile_notes": profile["source_notes"],
            "source_id": source_id,
            "execution_policy_ref": "bhoonidhi-default",
            "requires_approved_runtime": True,
        },
    }

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
    },
    LANDSAT_SOURCE_ID: {
        "status": "manual_only",
        "execution_policy_ref": "planetary-computer-default",
        "validation_profile": {
            "version": LANDSAT_MASK_PROFILE_VERSION,
            "checks": [
                "landsat_8_9_tier1_identity",
                "collection_2_reflectance_metadata",
                "qa_pixel_bitmask_validation",
                "qa_radsat_validation",
                "source_cog_mirror_validation",
                "field_index_response_validation",
            ],
        },
        "processing_profile": {
            "version": LANDSAT_PROCESSING_PROFILE_VERSION,
            "source": "planetary-computer",
            "level": "Collection-2-Level-2",
            "platforms": ["landsat-8", "landsat-9"],
            "reflectance_scale": LANDSAT_REFLECTANCE_SCALE,
            "reflectance_offset": LANDSAT_REFLECTANCE_OFFSET,
            "mask": LANDSAT_MASK_PROFILE_VERSION,
        },
        "license_profile": {
            "profile": "usgs-open-data",
            "serving": "internal_private",
        },
        "provider_metadata": {
            "primary_route": LANDSAT_PRIMARY_PROVIDER_ROUTE,
            "provider_collection": LANDSAT_PROVIDER_COLLECTION,
            "pgstac_collection": LANDSAT_PGSTAC_COLLECTION_ID,
            "signed_asset_access": True,
            "persist_signed_urls": False,
            "product_exposure": "hidden",
        },
    },
    EOS04_SOURCE_ID: {
        "status": "manual_only",
        "execution_policy_ref": "bhoonidhi-default",
        "validation_profile": {
            "version": EOS04_PROCESSING_PROFILE_VERSION,
            "checks": [
                "l2b_ard_metadata_validation",
                "rtc_applied_validation",
                "gamma0_dn_calibration_validation",
                "valid_data_mask_128_validation",
                "explicit_sar_polarizations",
                "float32_db_backscatter_cog",
                "sar_pgstac_metadata",
                "natural_tile_response_validation",
            ],
        },
        "processing_profile": {
            "version": EOS04_PROCESSING_PROFILE_VERSION,
            "source": "bhoonidhi",
            "level": "L2B",
            "family": "sar_backscatter",
            "input_representation": "uint16_gamma0_dn",
            "calibration_formula": (
                "10*log10(DN^2-IMAGE_NOISE_BIAS)-Calibration_Constant_Beta0"
            ),
            "valid_mask_value": 128,
            "output_unit": "dB",
        },
        "license_profile": {
            "profile": "nrsc-bhoonidhi-restricted",
            "serving": "internal_private",
        },
        "provider_metadata": {
            "primary_route": f"bhoonidhi:{EOS04_COLLECTION_ID}",
            "provider_collection": EOS04_COLLECTION_ID,
            "pgstac_collection": EOS04_PGSTAC_COLLECTION_ID,
            "requires_approved_runtime": True,
        },
    },
    NISAR_SOURCE_ID: {
        "status": "manual_only",
        "execution_policy_ref": "bhoonidhi-default",
        "validation_profile": {
            "version": NISAR_PROCESSING_PROFILE_VERSION,
            "checks": [
                "ssar_l2_gcov_identification",
                "gamma0_normalization",
                "rtc_applied_validation",
                "native_gcov_mask_validation",
                "explicit_sar_polarizations",
                "float32_db_backscatter_cog",
                "sar_pgstac_metadata",
            ],
        },
        "processing_profile": {
            "version": NISAR_PROCESSING_PROFILE_VERSION,
            "source": "bhoonidhi",
            "level": "L2-GCOV",
            "family": "sar_backscatter",
            "input_representation": "float32_gamma0_power",
            "calibration_formula": "10*log10(gamma0_power)",
            "output_unit": "dB",
        },
        "license_profile": {
            "profile": "nrsc-bhoonidhi-restricted",
            "serving": "internal_private",
        },
        "provider_metadata": {
            "primary_route": f"bhoonidhi:{NISAR_COLLECTION_ID}",
            "provider_collection": NISAR_COLLECTION_ID,
            "pgstac_collection": NISAR_PGSTAC_COLLECTION_ID,
            "requires_approved_runtime": True,
            "product_exposure": "hidden",
        },
    },
} | {
    source_id: _resourcesat_source_metadata(source_id, profile)
    for source_id, profile in RESOURCESAT_SOURCE_PROFILES.items()
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
        "provider_adapter": "planetary-computer",
        "provider_collection": "landsat-c2-l2",
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "signed_https",
        "execution_policy_ref": "planetary-computer-default",
        "license_profile": "usgs-open-data",
        "metadata": {
            "route_key": "planetary-computer:landsat-c2-l2",
            "sas_token_required": True,
            "persist_signed_urls": False,
        },
    },
    {
        "source_id": "landsat-c2-l2",
        "provider_adapter": "earthsearch",
        "provider_collection": "landsat-c2-l2",
        "provider_priority": 50,
        "provider_role": "fallback",
        "status": "inactive",
        "access_mode": "requester_pays_s3",
        "execution_policy_ref": "earthsearch-requester-pays",
        "license_profile": "usgs-open-data",
        "metadata": {
            "route_key": "earthsearch:landsat-c2-l2",
            "requester_pays": True,
            "explicit_billing_approval_required": True,
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
    {
        "source_id": RESOURCESAT_LISS3_BOA_SOURCE_ID,
        "provider_adapter": "bhoonidhi",
        "provider_collection": BHOONIDHI_LISS3_BOA_COLLECTION_ID,
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "authenticated_download",
        "execution_policy_ref": "bhoonidhi-default",
        "license_profile": "nrsc-bhoonidhi-restricted",
        "metadata": {
            "route_key": f"bhoonidhi:{BHOONIDHI_LISS3_BOA_COLLECTION_ID}",
            "source_notes": RESOURCESAT_SOURCE_PROFILES[RESOURCESAT_LISS3_BOA_SOURCE_ID][
                "source_notes"
            ],
            "requires_approved_runtime": True,
            "mask_method": RESOURCESAT_MASK_METHOD,
        },
    },
    {
        "source_id": RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
        "provider_adapter": "bhoonidhi",
        "provider_collection": BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "authenticated_download",
        "execution_policy_ref": "bhoonidhi-default",
        "license_profile": "nrsc-bhoonidhi-restricted",
        "metadata": {
            "route_key": f"bhoonidhi:{BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID}",
            "source_notes": RESOURCESAT_SOURCE_PROFILES[RESOURCESAT_LISS4_MX70_L2_SOURCE_ID][
                "source_notes"
            ],
            "requires_approved_runtime": True,
            "mask_method": RESOURCESAT_MASK_METHOD,
        },
    },
    {
        "source_id": RESOURCESAT_AWIFS_BOA_SOURCE_ID,
        "provider_adapter": "bhoonidhi",
        "provider_collection": BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "authenticated_download",
        "execution_policy_ref": "bhoonidhi-default",
        "license_profile": "nrsc-bhoonidhi-restricted",
        "metadata": {
            "route_key": f"bhoonidhi:{BHOONIDHI_AWIFS_BOA_COLLECTION_ID}",
            "source_notes": RESOURCESAT_SOURCE_PROFILES[RESOURCESAT_AWIFS_BOA_SOURCE_ID][
                "source_notes"
            ],
            "requires_approved_runtime": True,
            "mask_method": RESOURCESAT_MASK_METHOD,
        },
    },
    {
        "source_id": EOS04_SOURCE_ID,
        "provider_adapter": "bhoonidhi",
        "provider_collection": EOS04_COLLECTION_ID,
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "authenticated_download",
        "execution_policy_ref": "bhoonidhi-default",
        "license_profile": "nrsc-bhoonidhi-restricted",
        "metadata": {
            "route_key": f"bhoonidhi:{EOS04_COLLECTION_ID}",
            "requires_approved_runtime": True,
            "processing_family": "sar_backscatter",
            "product_exposure": "hidden",
        },
    },
    {
        "source_id": NISAR_SOURCE_ID,
        "provider_adapter": "bhoonidhi",
        "provider_collection": NISAR_COLLECTION_ID,
        "provider_priority": 1,
        "provider_role": "primary",
        "status": "manual_only",
        "access_mode": "authenticated_download",
        "execution_policy_ref": "bhoonidhi-default",
        "license_profile": "nrsc-bhoonidhi-restricted",
        "metadata": {
            "route_key": f"bhoonidhi:{NISAR_COLLECTION_ID}",
            "requires_approved_runtime": True,
            "processing_family": "sar_backscatter",
            "product_exposure": "hidden",
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
    {
        "index_name": "msavi",
        "value_domain_min": -1,
        "value_domain_max": 1,
        "display_min": -0.1,
        "display_max": 0.8,
        "palette_json": [
            {"value": -0.1, "color": "#8c510a"},
            {"value": 0.2, "color": "#f6e8c3"},
            {"value": 0.5, "color": "#7fbf7b"},
            {"value": 0.8, "color": "#1b7837"},
        ],
        "nodata_color": "transparent",
        "version": "msavi-default-v1",
        "is_default": True,
    },
    {
        "index_name": "ndmi",
        "value_domain_min": -1,
        "value_domain_max": 1,
        "display_min": -0.6,
        "display_max": 0.8,
        "palette_json": [
            {"value": -0.6, "color": "#8c510a"},
            {"value": -0.1, "color": "#dfc27d"},
            {"value": 0.3, "color": "#80cdc1"},
            {"value": 0.8, "color": "#01665e"},
        ],
        "nodata_color": "transparent",
        "version": "ndmi-default-v1",
        "is_default": True,
    },
    {
        "index_name": "ndwi_green_nir",
        "value_domain_min": -1,
        "value_domain_max": 1,
        "display_min": -0.6,
        "display_max": 0.6,
        "palette_json": [
            {"value": -0.6, "color": "#a6611a"},
            {"value": -0.1, "color": "#dfc27d"},
            {"value": 0.2, "color": "#80cdc1"},
            {"value": 0.6, "color": "#018571"},
        ],
        "nodata_color": "transparent",
        "version": "ndwi-green-nir-default-v1",
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
    *(
        {
            "profile_key": f"resourcesat-{source_id}-{index_name}-v1",
            "index_name": index_name,
            "crop": None,
            "season": None,
            "aoi_id": None,
            "source_id": source_id,
            "classes_json": classes,
            "is_default": True,
            "version": "resourcesat-thresholds-v1",
        }
        for source_id, profile in RESOURCESAT_SOURCE_PROFILES.items()
        for index_name, classes in {
            "ndvi": [
                {"label": "Bare or stressed", "min": -1.0, "max": 0.2},
                {"label": "Moderate vegetation", "min": 0.2, "max": 0.5},
                {"label": "Healthy crop", "min": 0.5, "max": 0.75},
                {"label": "Dense vegetation", "min": 0.75, "max": 1.0},
            ],
            "msavi": [
                {"label": "Low vegetation cover", "min": -1.0, "max": 0.2},
                {"label": "Moderate vegetation cover", "min": 0.2, "max": 0.5},
                {"label": "High vegetation cover", "min": 0.5, "max": 1.0},
            ],
            "ndmi": [
                {"label": "Dry vegetation", "min": -1.0, "max": -0.1},
                {"label": "Moderate moisture", "min": -0.1, "max": 0.3},
                {"label": "High moisture", "min": 0.3, "max": 1.0},
            ],
            "ndwi_green_nir": [
                {"label": "Low water signal", "min": -1.0, "max": -0.1},
                {"label": "Mixed moisture signal", "min": -0.1, "max": 0.2},
                {"label": "Water or high moisture", "min": 0.2, "max": 1.0},
            ],
        }.items()
        if index_name in profile["supported_indices"]
    ),
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


def _execution_policy(
    *,
    policy_key: str,
    provider_adapter: str,
    auth_model: str,
    enabled: bool,
    version: str,
    source_id: str | None = None,
    retry_policy: dict[str, Any] | None = None,
    staging_policy: dict[str, Any] | None = None,
    checksum_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "policy_key": policy_key,
        "provider_adapter": provider_adapter,
        "source_id": source_id,
        "auth_model": auth_model,
        "enabled": enabled,
        "version": version,
        "retry_policy_json": json.dumps(retry_policy or DEFAULT_RETRY_POLICY),
        "staging_policy_json": json.dumps(staging_policy or DEFAULT_STAGING_POLICY),
        "checksum_policy_json": json.dumps(checksum_policy or DEFAULT_CHECKSUM_POLICY),
    }


def build_execution_policies() -> list[dict[str, Any]]:
    return [
        _execution_policy(
            policy_key="mock-default",
            provider_adapter="mock",
            auth_model="none",
            enabled=True,
            version="phase1-v1",
        ),
        _execution_policy(
            policy_key="earthsearch-default",
            provider_adapter="earthsearch",
            auth_model="none",
            enabled=True,
            version="phase2-earthsearch-v1",
        ),
        _execution_policy(
            policy_key="planetary-computer-default",
            provider_adapter="planetary-computer",
            auth_model="anonymous_sas_signing",
            enabled=True,
            version="landsat-c2-l2-planetary-computer-v1",
        ),
        _execution_policy(
            policy_key="earthsearch-requester-pays",
            provider_adapter="earthsearch",
            auth_model="aws_requester_pays",
            enabled=False,
            version="landsat-c2-l2-earthsearch-requester-pays-v1",
        ),
        _execution_policy(
            policy_key="cdse-default",
            provider_adapter="cdse",
            auth_model="oauth2",
            enabled=False,
            version="phase1-v1",
        ),
        _execution_policy(
            policy_key="bhoonidhi-default",
            provider_adapter="bhoonidhi",
            auth_model="session_cookie_or_api_key",
            enabled=False,
            version="phase3-bhoonidhi-v1",
            retry_policy=BHOONIDHI_RETRY_POLICY,
            staging_policy=BHOONIDHI_STAGING_POLICY,
            checksum_policy=BHOONIDHI_CHECKSUM_POLICY,
        ),
        _execution_policy(
            policy_key="usgs-default",
            provider_adapter="usgs",
            auth_model="api_token",
            enabled=False,
            version="phase1-v1",
        ),
        _execution_policy(
            policy_key="earthdata-default",
            provider_adapter="earthdata",
            auth_model="basic_or_token",
            enabled=False,
            version="phase1-v1",
        ),
    ]


def seed_execution_policies(connection) -> None:
    policies = build_execution_policies()
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
                    CAST(:retry_policy_json AS jsonb),
                    CAST(:staging_policy_json AS jsonb),
                    CAST(:checksum_policy_json AS jsonb),
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
