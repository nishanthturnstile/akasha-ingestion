from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import PurePath
from typing import Any
from uuid import UUID

import pystac
from sqlalchemy import Engine, text

from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.processing.eos04 import (
    EOS04_COLLECTION_ID,
    EOS04_PGSTAC_COLLECTION_ID,
    EOS04_PROCESSING_PROFILE_VERSION,
    EOS04_SOURCE_ID,
)
from akasha.processing.resourcesat import (
    RESOURCESAT_MASK_CLASSES,
    RESOURCESAT_SOURCE_IDS,
    ResourceSatProfile,
    profile_for_source,
)

PHASE2_DERIVED_COLLECTION_ID = "akasha-sentinel-2-l2a-derived-v1"
RESOURCESAT_LISS3_DERIVED_COLLECTION_ID = "akasha-resourcesat-2a-liss3-boa-derived-v1"
RESOURCESAT_LISS4_DERIVED_COLLECTION_ID = "akasha-resourcesat-2a-liss4-mx70-l2-derived-v1"
RESOURCESAT_AWIFS_DERIVED_COLLECTION_ID = "akasha-resourcesat-2a-awifs-boa-derived-v1"
RESOURCESAT_DERIVED_COLLECTION_IDS = (
    RESOURCESAT_LISS3_DERIVED_COLLECTION_ID,
    RESOURCESAT_LISS4_DERIVED_COLLECTION_ID,
    RESOURCESAT_AWIFS_DERIVED_COLLECTION_ID,
)

EO_EXTENSION = "https://stac-extensions.github.io/eo/v2.0.0/schema.json"
RASTER_EXTENSION = "https://stac-extensions.github.io/raster/v2.0.0/schema.json"
PROJECTION_EXTENSION = "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
SAR_EXTENSION = "https://stac-extensions.github.io/sar/v1.3.0/schema.json"
CLASSIFICATION_EXTENSION = (
    "https://stac-extensions.github.io/classification/v2.0.0/schema.json"
)
DERIVED_STAC_EXTENSIONS = (
    EO_EXTENSION,
    RASTER_EXTENSION,
    PROJECTION_EXTENSION,
    CLASSIFICATION_EXTENSION,
)


class PgstacRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_item_json(self, item: pystac.Item) -> None:
        item_dict = item.to_dict()
        collection = collection_json(item.collection_id or PHASE2_DERIVED_COLLECTION_ID)
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pgstac.upsert_collection(CAST(:collection AS jsonb))"),
                {"collection": _json_dumps(collection)},
            )
            connection.execute(
                text("SELECT pgstac.upsert_item(CAST(:item AS jsonb))"),
                {"item": _json_dumps(item_dict)},
            )


def build_resourcesat_derived_item(
    *,
    scene: ProviderSceneRecord,
    outputs: list[RasterOutputRecord],
    bbox: list[float],
    geometry: dict[str, Any],
) -> pystac.Item:
    profile = profile_for_source(scene.source_id)
    properties = {
        "platform": "resourcesat-2a",
        "instruments": [profile.instrument.lower()],
        "akasha:source_id": scene.source_id,
        "akasha:provider_adapter": scene.provider_adapter,
        "akasha:provider_collection": profile.collection_id,
        "akasha:logical_scene_key": scene.logical_scene_key,
        "akasha:aoi_id": scene.aoi_id,
        "akasha:resource_sat_product_id": scene.provider_product_id,
        "akasha:analysis_level": profile.analysis_level,
        "akasha:mask_method": scene.provider_metadata.get(
            "mask_method", "akasha-threshold-mask-v1"
        ),
        "akasha:metrics_provisional": bool(
            scene.provider_metadata.get("metrics_provisional", True)
        ),
        "classification:classes": _classification_classes(),
    }
    stac_extensions = list(DERIVED_STAC_EXTENSIONS)
    cloud_cover = _cloud_cover(scene)
    if cloud_cover is not None:
        properties["eo:cloud_cover"] = cloud_cover
    else:
        stac_extensions.remove(EO_EXTENSION)
    item = _base_derived_item(
        scene=scene,
        item_id=scene.pgstac_item_id or _resourcesat_derived_item_id(scene, profile),
        collection_id=profile.pgstac_collection,
        bbox=bbox,
        geometry=geometry,
        stac_extensions=stac_extensions,
        properties=properties,
    )
    _apply_projection_properties(item, outputs)
    for output in outputs:
        if output.index_name is None:
            continue
        index_name = profile.require_index(output.index_name)
        item.add_asset(
            index_name,
            pystac.Asset(
                href=str(output.metadata.get("pgstac_href") or output.object_path),
                media_type=pystac.MediaType.COG,
                roles=["data", "derived", index_name],
                title=f"{index_name.upper()} ResourceSat derived index",
                extra_fields=_derived_asset_fields(output),
            ),
        )
    return item


def build_derived_item(
    *,
    scene: ProviderSceneRecord,
    outputs: list[RasterOutputRecord],
    bbox: list[float],
    geometry: dict[str, Any],
) -> pystac.Item:
    if scene.source_id in RESOURCESAT_SOURCE_IDS:
        return build_resourcesat_derived_item(
            scene=scene,
            outputs=outputs,
            bbox=bbox,
            geometry=geometry,
        )
    item = _base_derived_item(
        scene=scene,
        item_id=scene.pgstac_item_id or _derived_item_id(scene),
        collection_id=PHASE2_DERIVED_COLLECTION_ID,
        bbox=bbox,
        geometry=geometry,
        stac_extensions=[],
        properties={
            "akasha:source_id": scene.source_id,
            "akasha:provider_adapter": scene.provider_adapter,
            "akasha:logical_scene_key": scene.logical_scene_key,
        },
    )
    for output in outputs:
        if output.index_name is None:
            continue
        item.add_asset(
            output.index_name,
            pystac.Asset(
                href=str(output.metadata.get("pgstac_href") or output.object_path),
                media_type=pystac.MediaType.COG,
                roles=["data"],
                extra_fields={
                    "akasha:formula_version": output.formula_version,
                    "akasha:processing_profile_version": output.processing_profile_version,
                },
            ),
        )
    return item


def build_eos04_backscatter_item(
    *,
    scene: ProviderSceneRecord,
    asset: SceneAssetRecord,
    bbox: list[float],
    geometry: dict[str, Any],
) -> pystac.Item:
    if scene.source_id != EOS04_SOURCE_ID:
        raise ValueError("EOS-04 STAC builder requires the EOS-04 source")
    polarizations = [
        str(value).upper() for value in asset.metadata.get("polarizations", []) if value
    ]
    if not polarizations:
        raise ValueError("EOS-04 STAC item requires explicit sar:polarizations")
    comparison = dict(scene.provider_metadata.get("comparison_metadata") or {})
    item_id = scene.pgstac_item_id or _eos04_item_id(scene)
    item = _base_derived_item(
        scene=scene,
        item_id=item_id,
        collection_id=EOS04_PGSTAC_COLLECTION_ID,
        bbox=bbox,
        geometry=geometry,
        stac_extensions=[SAR_EXTENSION, RASTER_EXTENSION, PROJECTION_EXTENSION],
        properties={
            "platform": "eos-04",
            "instruments": ["sar"],
            "sar:frequency_band": "C",
            "sar:instrument_mode": "MRS",
            "sar:polarizations": polarizations,
            "akasha:source_id": EOS04_SOURCE_ID,
            "akasha:provider_adapter": scene.provider_adapter,
            "akasha:provider_collection": EOS04_COLLECTION_ID,
            "akasha:logical_scene_key": scene.logical_scene_key,
            "akasha:aoi_id": scene.aoi_id,
            "akasha:processing_profile_version": EOS04_PROCESSING_PROFILE_VERSION,
            "akasha:comparison_policy_version": comparison.get("policyVersion"),
            "akasha:comparison_key_hash": comparison.get("keyHash"),
            "akasha:comparison_metadata_complete": bool(comparison.get("complete")),
            "akasha:orbit_state": comparison.get("orbitState"),
            "akasha:track_key": comparison.get("trackKey"),
            "akasha:incidence_angle_degrees": comparison.get("incidenceAngleDegrees"),
            "akasha:sensor_orientation": comparison.get("sensorOrientation"),
            "akasha:rtc_applied": comparison.get("rtcApplied"),
            "akasha:display_only": True,
        },
    )
    item.add_asset(
        "backscatter",
        pystac.Asset(
            href=str(asset.asset_href or asset.object_path),
            media_type=pystac.MediaType.COG,
            roles=["data", "backscatter"],
            title="EOS-04 calibrated backscatter",
            extra_fields={
                "raster:bands": [
                    {
                        "name": polarization,
                        "data_type": "float32",
                        "nodata": asset.nodata_value,
                        "unit": "dB",
                    }
                    for polarization in polarizations
                ]
            },
        ),
    )
    return item


def _base_derived_item(
    *,
    scene: ProviderSceneRecord,
    item_id: str,
    collection_id: str,
    bbox: list[float],
    geometry: dict[str, Any],
    stac_extensions: list[str] | tuple[str, ...],
    properties: dict[str, Any],
) -> pystac.Item:
    item = pystac.Item(
        id=item_id,
        geometry=geometry,
        bbox=bbox,
        datetime=scene.acquisition_at or datetime.now(UTC),
        properties=properties,
        stac_extensions=list(stac_extensions),
    )
    item.collection_id = collection_id
    item.add_link(
        pystac.Link(
            rel=pystac.RelType.COLLECTION,
            target=f"/collections/{collection_id}",
            media_type=pystac.MediaType.JSON,
        )
    )
    return item


def _derived_item_id(scene: ProviderSceneRecord) -> str:
    logical = scene.logical_scene_key or scene.provider_product_id
    acquisition = (
        scene.acquisition_at.strftime("%Y%m%dT%H%M%S") if scene.acquisition_at else "unknown"
    )
    product_hash = sha256(logical.encode()).hexdigest()[:12]
    mgrs_or_group = str(scene.provider_metadata.get("mgrs_tile") or "group")
    return f"s2-l2a-{mgrs_or_group}-{acquisition}-{product_hash}"


def _resourcesat_derived_item_id(
    scene: ProviderSceneRecord,
    profile: ResourceSatProfile,
) -> str:
    logical = scene.logical_scene_key or scene.provider_product_id
    acquisition = (
        scene.acquisition_at.strftime("%Y%m%dT%H%M%S") if scene.acquisition_at else "unknown"
    )
    product_hash = sha256(logical.encode()).hexdigest()[:12]
    aoi_or_group = scene.aoi_id or str(scene.provider_metadata.get("aoi_id") or "group")
    source_slug = profile.source_id.replace("resourcesat-2a-", "rs2a-")
    return f"{source_slug}-{aoi_or_group}-{acquisition}-{product_hash}"


def _eos04_item_id(scene: ProviderSceneRecord) -> str:
    logical = scene.logical_scene_key or scene.provider_product_id
    acquisition = (
        scene.acquisition_at.strftime("%Y%m%dT%H%M%S") if scene.acquisition_at else "unknown"
    )
    product_hash = sha256(logical.encode()).hexdigest()[:12]
    return f"eos04-sar-mrs-{acquisition}-{product_hash}"


def _json_dumps(value: dict[str, Any]) -> str:
    from json import dumps

    return dumps(value, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("Non-finite Decimal values are not valid STAC JSON.")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not STAC JSON serializable.")


def collection_json(collection_id: str) -> dict[str, Any]:
    if collection_id == PHASE2_DERIVED_COLLECTION_ID:
        return _sentinel2_collection(collection_id)
    for source_id in RESOURCESAT_SOURCE_IDS:
        profile = profile_for_source(source_id)
        if collection_id == profile.pgstac_collection:
            return _resourcesat_collection(profile)
    if collection_id == EOS04_PGSTAC_COLLECTION_ID:
        return _eos04_collection(collection_id)
    return _generic_collection(collection_id)


def _collection(collection_id: str) -> dict[str, Any]:
    return collection_json(collection_id)


def _sentinel2_collection(collection_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "title": "Akasha Sentinel-2 L2A derived vegetation indices",
        "description": "Akasha Sentinel-2 L2A derived vegetation index COGs.",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[now, None]]},
        },
        "links": [],
    }


def _resourcesat_collection(profile: ResourceSatProfile) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "stac_extensions": list(DERIVED_STAC_EXTENSIONS),
        "id": profile.pgstac_collection,
        "title": (
            f"Akasha ResourceSat-2A {profile.instrument} "
            f"{profile.analysis_level} derived indices"
        ),
        "description": (
            f"Akasha ResourceSat-2A {profile.instrument} {profile.analysis_level} "
            "cloud-masked derived vegetation index COGs generated from Bhoonidhi "
            "products with Akasha threshold mask v1."
        ),
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[now, None]]},
        },
        "summaries": {
            "platform": ["resourcesat-2a"],
            "instruments": [profile.instrument.lower()],
            "akasha:source_id": [profile.source_id],
            "akasha:provider_collection": [profile.collection_id],
            "akasha:supported_indices": list(profile.supported_indices),
            "akasha:mask_method": ["akasha-threshold-mask-v1"],
            "classification:classes": _classification_classes(),
            "bands": _source_band_summaries(profile),
        },
        "links": [],
    }


def _eos04_collection(collection_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "stac_extensions": [SAR_EXTENSION, RASTER_EXTENSION, PROJECTION_EXTENSION],
        "id": collection_id,
        "title": "Akasha EOS-04 SAR-MRS L2B backscatter",
        "description": (
            "Validated EOS-04 C-band SAR-MRS L2B calibrated backscatter COGs from "
            "ISRO/NRSC Bhoonidhi. Display-only; no optical vegetation indices."
        ),
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[now, None]]},
        },
        "summaries": {
            "platform": ["eos-04"],
            "instruments": ["sar"],
            "sar:frequency_band": ["C"],
            "sar:instrument_mode": ["MRS"],
            "akasha:source_id": [EOS04_SOURCE_ID],
            "akasha:provider_collection": [EOS04_COLLECTION_ID],
            "akasha:supported_indices": [],
        },
        "links": [],
    }


def _generic_collection(collection_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "title": "Akasha derived raster outputs",
        "description": "Akasha derived raster output COGs.",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[now, None]]},
        },
        "links": [],
    }


def _derived_asset_fields(output: RasterOutputRecord) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "akasha:formula_version": output.formula_version,
        "akasha:processing_profile_version": output.processing_profile_version,
        "bands": [
            {
                "name": output.index_name,
                "nodata": output.nodata_value,
                "data_type": output.dtype,
                "raster:spatial_resolution": output.processing_resolution,
                "raster:scale": (1.0 / output.scale_factor) if output.scale_factor else None,
                "raster:offset": output.offset or 0.0,
            }
        ],
    }
    if output.crs:
        fields["proj:code"] = output.crs
    proj_shape = output.metadata.get("proj_shape")
    if proj_shape is not None:
        fields["proj:shape"] = list(proj_shape)
    proj_transform = output.metadata.get("proj_transform")
    if proj_transform is not None:
        fields["proj:transform"] = list(proj_transform)
    return fields


def _apply_projection_properties(
    item: pystac.Item,
    outputs: list[RasterOutputRecord],
) -> None:
    first = next((output for output in outputs if output.index_name is not None), None)
    if first is None:
        return
    if first.crs:
        item.properties["proj:code"] = first.crs
    proj_shape = first.metadata.get("proj_shape")
    if proj_shape is not None:
        item.properties["proj:shape"] = list(proj_shape)
    proj_transform = first.metadata.get("proj_transform")
    if proj_transform is not None:
        item.properties["proj:transform"] = list(proj_transform)
    proj_bbox = first.metadata.get("proj_bbox")
    if proj_bbox is not None:
        item.properties["proj:bbox"] = list(proj_bbox)


def _classification_classes() -> list[dict[str, Any]]:
    return [
        {
            "value": item.value,
            "name": item.label,
            "title": item.label.replace("_", " ").title(),
            "description": item.description,
            "nodata": item.value == 0,
        }
        for item in RESOURCESAT_MASK_CLASSES
    ]


def _source_band_summaries(profile: ResourceSatProfile) -> list[dict[str, Any]]:
    common_names = {
        "GREEN": "green",
        "RED": "red",
        "NIR": "nir",
        "SWIR1": "swir16",
    }
    return [
        {
            "name": band_name,
            "eo:common_name": common_names.get(role, role.lower()),
            "raster:spatial_resolution": profile.native_resolution_m,
            "raster:scale": 0.0001,
            "raster:offset": 0.0,
            "akasha:role": role,
        }
        for role, band_name in profile.band_roles.items()
    ]


def _cloud_cover(scene: ProviderSceneRecord) -> float | None:
    if scene.cloud_percent is not None:
        return float(scene.cloud_percent)
    for key in ("cloud_masked_percent", "cloud_percent"):
        value = scene.provider_metadata.get(key)
        if value is not None:
            return float(value)
    return None
