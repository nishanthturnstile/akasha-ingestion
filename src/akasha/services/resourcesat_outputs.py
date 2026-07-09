from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np

from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.pgstac_repository import build_resourcesat_derived_item
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.catalog.tile_layer_repository import TileLayerRecord
from akasha.config import Settings, validate_resourcesat_runtime_roots
from akasha.processing.cog import cog_metadata, write_cog_file
from akasha.processing.indices import encode_index_output
from akasha.processing.resourcesat import (
    RESOURCESAT_MASK_METHOD,
    ResourceSatProfile,
    calculate_resourcesat_index,
    profile_for_source,
    reflectance_from_dn,
    resourcesat_output_profile,
    resourcesat_valid_mask,
)
from akasha.storage.object_store import file_sha256

BHOONIDHI_PROVIDER = "bhoonidhi"
RESOURCESAT_SCENE_OUTPUT_KIND = "resource_sat_scene"
RESOURCESAT_COMPOSITE_OUTPUT_KIND = "resource_sat_composite"
RESOURCESAT_DERIVED_OUTPUT_KIND = "derived_index"


@dataclass(frozen=True, slots=True)
class ResourceSatDerivedOutputResult:
    scene: ProviderSceneRecord
    outputs: list[RasterOutputRecord]
    local_paths: dict[str, Path]


def provider_scene_from_prepare_manifest(
    manifest: dict[str, Any],
    *,
    raw_object_path: str | None = None,
    provider_route_id: str | None = None,
) -> ProviderSceneRecord:
    profile = profile_for_source(str(manifest.get("source_id") or ""))
    product_id = _required_str(manifest, "product_id")
    acquisition_at = _required_datetime(manifest, "acquisition_datetime")
    return ProviderSceneRecord(
        id=None,
        provider_adapter=BHOONIDHI_PROVIDER,
        source_id=profile.source_id,
        provider_product_id=product_id,
        acquisition_at=acquisition_at,
        scene_geometry=_required_geometry(manifest),
        status="prepared",
        license_state="provider_restricted",
        provider_metadata={
            "provider_collection": profile.collection_id,
            "output_kind": RESOURCESAT_SCENE_OUTPUT_KIND,
            "path": manifest.get("path"),
            "row": manifest.get("row"),
            "mask_method": manifest.get("mask_method"),
            "metrics_provisional": manifest.get("akasha:metrics_provisional"),
        },
        aoi_id=str(manifest.get("aoi_id") or ""),
        provider_route_id=provider_route_id,
        logical_scene_key=_scene_logical_key(profile, product_id, acquisition_at),
        native_crs=str(manifest.get("crs") or ""),
        native_resolution=profile.native_resolution_m,
        raw_object_path=raw_object_path,
    )


def provider_scene_from_composite_manifest(
    manifest: dict[str, Any],
    *,
    provider_route_id: str | None = None,
) -> ProviderSceneRecord:
    profile = profile_for_source(str(manifest.get("source_id") or ""))
    composite_datetime = _required_datetime(manifest, "composite_datetime")
    composite_id = _composite_product_id(manifest)
    metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
    grid = manifest.get("grid") if isinstance(manifest.get("grid"), dict) else {}
    return ProviderSceneRecord(
        id=None,
        provider_adapter=BHOONIDHI_PROVIDER,
        source_id=profile.source_id,
        provider_product_id=composite_id,
        acquisition_at=composite_datetime,
        scene_geometry=_required_geometry(manifest),
        status="composited",
        cloud_percent=metrics.get("cloud_masked_percent"),
        license_state="provider_restricted",
        provider_metadata={
            "provider_collection": profile.collection_id,
            "output_kind": RESOURCESAT_COMPOSITE_OUTPUT_KIND,
            "composite": True,
            "composite_date": manifest.get("composite_date"),
            "mask_method": manifest.get("mask_method"),
            "metrics_provisional": manifest.get("akasha:metrics_provisional"),
            "coverage_percent": metrics.get("coverage_percent"),
            "usable_pixel_percent": metrics.get("usable_pixel_percent"),
            "contributing_scenes": metrics.get("contributing_scenes") or [],
            "grid": grid,
        },
        aoi_id=str(manifest.get("aoi_id") or ""),
        provider_route_id=provider_route_id,
        logical_scene_key=_scene_logical_key(profile, composite_id, composite_datetime),
        native_crs=str(grid.get("crs") or ""),
        native_resolution=float(grid.get("resolution") or profile.native_resolution_m),
        coverage_percentage=metrics.get("coverage_percent"),
    )


def scene_asset_records_from_prepare_manifest(
    scene: ProviderSceneRecord,
    manifest: dict[str, Any],
    *,
    raw_object_path: str | None = None,
    raw_checksum_sha256: str | None = None,
    raw_size_bytes: int | None = None,
) -> list[SceneAssetRecord]:
    outputs = _required_outputs(manifest)
    records: list[SceneAssetRecord] = []
    if raw_object_path:
        records.append(
            SceneAssetRecord(
                id=None,
                scene_id=scene.id or "",
                asset_kind="raw_package",
                asset_key="raw_zip",
                object_path=raw_object_path,
                checksum_sha256=raw_checksum_sha256,
                size_bytes=raw_size_bytes,
                storage_backend="minio",
                media_type="application/zip",
                mirror_status="not_required",
                metadata={"provider": BHOONIDHI_PROVIDER},
            )
        )
    records.extend(
        _scene_asset_record(scene, manifest, asset_key, asset_kind=f"prepared_{asset_key}")
        for asset_key in ("analytic", "mask")
        if asset_key in outputs
    )
    return records


def scene_asset_records_from_composite_manifest(
    scene: ProviderSceneRecord,
    manifest: dict[str, Any],
) -> list[SceneAssetRecord]:
    outputs = _required_outputs(manifest)
    return [
        _scene_asset_record(scene, manifest, asset_key, asset_kind=f"composite_{asset_key}")
        for asset_key in ("analytic", "mask")
        if asset_key in outputs
    ]


def generate_resourcesat_derived_indices(
    *,
    manifest_path: Path,
    scene: ProviderSceneRecord,
    output_root: Path,
    settings: Settings,
    object_store: Any,
    raster_repository: Any | None = None,
    tile_layer_repository: Any | None = None,
    pgstac_repository: Any | None = None,
    scene_repository: Any | None = None,
    dry_run: bool = False,
) -> ResourceSatDerivedOutputResult:
    import rasterio

    validate_resourcesat_runtime_roots(settings, dry_run=dry_run)
    if not scene.id:
        raise ValueError("ResourceSat derived outputs require a persisted scene id")
    manifest = _read_manifest(manifest_path)
    profile = profile_for_source(str(manifest.get("source_id") or scene.source_id))
    if scene.source_id != profile.source_id:
        raise ValueError("scene source_id does not match ResourceSat manifest")
    analytic_path = _resolve_output_path(manifest_path, manifest, "analytic")
    mask_path = _resolve_output_path(manifest_path, manifest, "mask")
    stac_item_id = scene.provider_product_id
    local_output_dir = output_root / profile.source_id / _safe_component(stac_item_id) / "indices"
    local_output_dir.mkdir(parents=True, exist_ok=True)

    records: list[RasterOutputRecord] = []
    local_paths: dict[str, Path] = {}
    with rasterio.open(analytic_path) as analytic, rasterio.open(mask_path) as mask:
        if analytic.count != len(profile.band_order):
            raise ValueError("analytic band count does not match ResourceSat profile")
        if mask.count != 1:
            raise ValueError("mask COG must have one band")
        mask_values = mask.read(1).astype("uint8", copy=False)
        valid_mask = resourcesat_valid_mask(mask_values)
        bands_by_name = {
            band_name: analytic.read(index).astype("uint16", copy=False)
            for index, band_name in enumerate(profile.band_order, start=1)
        }
        for index_name in profile.supported_indices:
            output_profile = resourcesat_output_profile(profile, index_name, settings=settings)
            bands_by_role = _reflectance_bands_for_index(
                profile,
                index_name,
                bands_by_name,
                valid_mask,
            )
            values = calculate_resourcesat_index(
                profile,
                index_name,
                bands_by_role,
                valid_mask=valid_mask,
            )
            encoded, resolved_profile = encode_index_output(
                index_name,
                values,
                profile=output_profile,
            )
            local_path = local_output_dir / f"{index_name}.cog.tif"
            write_cog_file(
                encoded,
                local_path,
                transform=analytic.transform,
                crs=str(analytic.crs),
                nodata=resolved_profile.nodata_value,
                tags={
                    "AKASHA_SOURCE_ID": profile.source_id,
                    "AKASHA_INDEX_NAME": index_name,
                    "AKASHA_FORMULA_VERSION": resolved_profile.formula_version,
                    "AKASHA_PROCESSING_PROFILE_VERSION": profile.processing_profile_version,
                    "AKASHA_MASK_METHOD": RESOURCESAT_MASK_METHOD,
                    "AKASHA_SOURCE_SCENE_ID": scene.id,
                    "AREA_OR_POINT": "Area",
                },
                band_descriptions=(index_name,),
                overview_resampling="bilinear",
            )
            checksum = file_sha256(local_path)
            object_path, object_checksum = object_store.put_derived_cog_file(
                provider=BHOONIDHI_PROVIDER,
                source_id=profile.source_id,
                stac_item_id=stac_item_id,
                index_name=index_name,
                file_path=local_path,
                checksum_sha256=checksum,
                metadata={
                    "source-id": profile.source_id,
                    "stac-item-id": stac_item_id,
                    "index-name": index_name,
                    "mask-method": RESOURCESAT_MASK_METHOD,
                },
            )
            metadata = cog_metadata(
                encoded,
                crs=str(analytic.crs),
                resolution=float(resolved_profile.processing_resolution),
                nodata=resolved_profile.nodata_value,
            )
            record = RasterOutputRecord(
                id=None,
                scene_id=scene.id,
                output_kind=RESOURCESAT_DERIVED_OUTPUT_KIND,
                index_name=index_name,
                object_path=object_path,
                checksum_sha256=object_checksum,
                formula_version=resolved_profile.formula_version,
                processing_profile_version=profile.processing_profile_version,
                dtype=resolved_profile.dtype,
                scale_factor=resolved_profile.scale_factor,
                offset=0.0,
                nodata_value=resolved_profile.nodata_value,
                min_value=metadata["min_value"],
                max_value=metadata["max_value"],
                native_resolution=profile.native_resolution_m,
                processing_resolution=float(resolved_profile.processing_resolution),
                display_resolution=float(resolved_profile.processing_resolution),
                crs=str(analytic.crs),
                cloud_mask_version=RESOURCESAT_MASK_METHOD,
                metadata={
                    "provider": BHOONIDHI_PROVIDER,
                    "source_id": profile.source_id,
                    "pgstac_collection": profile.pgstac_collection,
                    "pgstac_asset_key": index_name,
                    "pgstac_href": f"s3://{settings.minio_bucket}/{object_path}",
                    "mask_method": RESOURCESAT_MASK_METHOD,
                    "mask_object_path": _manifest_object_path(manifest, "mask"),
                    "proj_shape": [int(analytic.height), int(analytic.width)],
                    "proj_transform": list(analytic.transform)[:6],
                    "proj_bbox": [
                        float(analytic.bounds.left),
                        float(analytic.bounds.bottom),
                        float(analytic.bounds.right),
                        float(analytic.bounds.top),
                    ],
                },
            )
            if raster_repository is not None:
                record = raster_repository.upsert_derived_index(record)
            if tile_layer_repository is not None:
                tile_layer_repository.upsert_for_raster(
                    TileLayerRecord(
                        layer_id=None,
                        raster_output_id=record.id or "",
                        visibility="private",
                        metadata={
                            "provider": BHOONIDHI_PROVIDER,
                            "source_id": profile.source_id,
                            "index_name": index_name,
                            "scene_id": scene.id,
                        },
                    )
                )
            records.append(record)
            local_paths[index_name] = local_path

    if pgstac_repository is not None:
        geometry = scene.scene_geometry or manifest.get("geometry")
        bbox = manifest.get("bbox")
        if not geometry or not bbox:
            raise ValueError("ResourceSat pgSTAC registration requires geometry and bbox")
        item = build_resourcesat_derived_item(
            scene=scene,
            outputs=records,
            bbox=[float(value) for value in bbox],
            geometry=geometry,
        )
        pgstac_repository.upsert_item_json(item)
        scene.pgstac_item_id = item.id
        if scene_repository is not None:
            scene = scene_repository.upsert(scene)

    return ResourceSatDerivedOutputResult(scene=scene, outputs=records, local_paths=local_paths)


def _reflectance_bands_for_index(
    profile: ResourceSatProfile,
    index_name: str,
    bands_by_name: dict[str, np.ndarray],
    valid_mask: np.ndarray,
) -> dict[str, np.ndarray]:
    roles = profile.band_roles_for_index(index_name)
    return {
        role: reflectance_from_dn(bands_by_name[profile.band_roles[role]], valid_mask)
        for role in roles
    }


def _scene_asset_record(
    scene: ProviderSceneRecord,
    manifest: dict[str, Any],
    asset_key: str,
    *,
    asset_kind: str,
) -> SceneAssetRecord:
    output = _required_outputs(manifest)[asset_key]
    return SceneAssetRecord(
        id=None,
        scene_id=scene.id or "",
        asset_kind=asset_kind,
        asset_key=asset_key,
        object_path=str(output.get("object_path") or output.get("path") or ""),
        checksum_sha256=str(output.get("checksum_sha256") or ""),
        size_bytes=int(output.get("size_bytes") or 0),
        storage_backend="minio" if output.get("object_path") else "local",
        nodata_value=output.get("nodata"),
        roles=["data"] if asset_key == "analytic" else ["metadata", "mask"],
        media_type="image/tiff; application=geotiff; profile=cloud-optimized",
        mirror_status="not_required",
        metadata={
            "provider": BHOONIDHI_PROVIDER,
            "source_id": scene.source_id,
            "mask_method": manifest.get("mask_method"),
            "output_kind": manifest.get("output_kind"),
            "dtype": output.get("dtype"),
            "band_count": output.get("band_count"),
        },
    )


def _composite_product_id(manifest: dict[str, Any]) -> str:
    source_id = _required_str(manifest, "source_id")
    aoi_id = _required_str(manifest, "aoi_id")
    composite_date = _required_str(manifest, "composite_date")
    return f"{source_id}:composite:{aoi_id}:{composite_date}"


def _scene_logical_key(
    profile: ResourceSatProfile,
    product_id: str,
    acquisition_at: datetime,
) -> str:
    return (
        f"{profile.source_id}:{profile.collection_id}:{product_id}:"
        f"{acquisition_at.isoformat()}"
    )


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_.=-]+", "-", value).strip(".-")
    if len(component) > 48:
        digest = sha256(value.encode("utf-8")).hexdigest()[:12]
        component = f"{component[:35].rstrip('.-')}-{digest}"
    return component or "resourcesat-output"


def _read_manifest(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _required_outputs(manifest: dict[str, Any]) -> dict[str, Any]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("ResourceSat manifest outputs missing or invalid")
    return outputs


def _resolve_output_path(manifest_path: Path, manifest: dict[str, Any], key: str) -> Path:
    output = _required_outputs(manifest).get(key)
    if not isinstance(output, dict) or not output.get("path"):
        raise ValueError(f"ResourceSat manifest missing {key} output path")
    path = Path(str(output["path"]))
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path


def _manifest_object_path(manifest: dict[str, Any], key: str) -> str | None:
    output = _required_outputs(manifest).get(key)
    if not isinstance(output, dict):
        return None
    return output.get("object_path")


def _required_str(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not value:
        raise ValueError(f"ResourceSat manifest missing {key}")
    return str(value)


def _required_datetime(manifest: dict[str, Any], key: str) -> datetime:
    value = _required_str(manifest, key)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _required_geometry(manifest: dict[str, Any]) -> dict[str, Any]:
    geometry = manifest.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("ResourceSat manifest geometry missing or invalid")
    return geometry
