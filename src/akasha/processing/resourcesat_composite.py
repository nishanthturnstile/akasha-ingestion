from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.warp import reproject, transform_geom
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

from akasha.catalog.aoi_repository import AoiRecord
from akasha.config import Settings, validate_resourcesat_runtime_roots
from akasha.processing.cog import validate_cog, write_cog_file
from akasha.processing.resourcesat import (
    RESOURCESAT_MASK_CLASSES,
    RESOURCESAT_MASK_METHOD,
    RESOURCESAT_VALID_MASK_CLASSES,
    ResourceSatProfile,
    profile_for_source,
)
from akasha.storage.object_store import file_sha256

COMPOSITE_OUTPUT_KIND = "resource_sat_composite"
COMPOSITE_GRID_CRS_KEYS = (
    "compositeGridCrs",
    "composite_grid_crs",
    "akasha:composite_grid_crs",
)


@dataclass(frozen=True, slots=True)
class CompositeGrid:
    crs: str
    bounds: tuple[float, float, float, float]
    resolution: float
    width: int
    height: int
    transform: Affine

    @classmethod
    def from_projected_bounds(
        cls,
        bounds: tuple[float, float, float, float] | list[float],
        *,
        crs: str,
        resolution: float,
        padding_pixels: int = 0,
    ) -> CompositeGrid:
        if len(bounds) != 4:
            raise ValueError("grid bounds must contain west, south, east, north")
        west, south, east, north = [float(value) for value in bounds]
        if west >= east or south >= north:
            raise ValueError(f"invalid projected bounds: {bounds}")
        if resolution <= 0:
            raise ValueError("grid resolution must be positive")
        pad = max(0, int(padding_pixels)) * resolution
        west = math.floor((west - pad) / resolution) * resolution
        south = math.floor((south - pad) / resolution) * resolution
        east = math.ceil((east + pad) / resolution) * resolution
        north = math.ceil((north + pad) / resolution) * resolution
        width = max(1, int(round((east - west) / resolution)))
        height = max(1, int(round((north - south) / resolution)))
        return cls(
            crs=crs,
            bounds=(west, south, east, north),
            resolution=resolution,
            width=width,
            height=height,
            transform=Affine(resolution, 0.0, west, 0.0, -resolution, north),
        )


@dataclass(frozen=True, slots=True)
class AlignedResourceSatScene:
    scene_id: str
    acquisition_at: datetime
    analytic: NDArray[np.uint16]
    mask: NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class ResourceSatCompositeBuildResult:
    output_dir: Path
    analytic_cog: Path
    mask_cog: Path
    manifest_path: Path
    manifest: dict[str, Any]
    metrics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResourceSatCompositeVerifyResult:
    ok: bool
    checks: list[str]
    problems: list[str]


@dataclass(frozen=True, slots=True)
class ResourceSatCompositePolicy:
    min_coverage_percent: float
    warnings: tuple[str, ...]


def composite_policy(
    profile: ResourceSatProfile,
    settings: Settings,
) -> ResourceSatCompositePolicy:
    if profile.instrument == "LISS-3":
        return ResourceSatCompositePolicy(
            min_coverage_percent=settings.resourcesat_liss3_composite_min_coverage_percent,
            warnings=(),
        )
    if profile.instrument == "LISS-4":
        return ResourceSatCompositePolicy(
            min_coverage_percent=settings.resourcesat_liss4_composite_min_coverage_percent,
            warnings=("partial_aoi_coverage_expected",),
        )
    return ResourceSatCompositePolicy(
        min_coverage_percent=settings.resourcesat_awifs_composite_min_coverage_percent,
        warnings=("coarse_regional_source",),
    )


def composite_grid_crs(aoi: AoiRecord) -> str:
    for key in COMPOSITE_GRID_CRS_KEYS:
        value = aoi.metadata.get(key)
        if value:
            crs = CRS.from_user_input(value)
            if not crs.is_projected:
                raise ValueError(f"composite grid CRS must be projected: {value}")
            return crs.to_string()
    centroid = shape(aoi.geometry).centroid
    return _utm_crs_for_lon_lat(lon=float(centroid.x), lat=float(centroid.y))


def grid_from_aoi(
    aoi: AoiRecord,
    profile: ResourceSatProfile,
    settings: Settings,
    *,
    padding_pixels: int = 0,
) -> CompositeGrid:
    crs = composite_grid_crs(aoi)
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    projected = shapely_transform(transformer.transform, shape(aoi.geometry))
    return CompositeGrid.from_projected_bounds(
        projected.bounds,
        crs=crs,
        resolution=profile.processing_resolution_m(settings),
        padding_pixels=padding_pixels,
    )


def aoi_mask_for_grid(aoi: AoiRecord, grid: CompositeGrid) -> NDArray[np.bool_]:
    projected = transform_geom("EPSG:4326", grid.crs, aoi.geometry)
    return geometry_mask(
        [projected],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        invert=True,
    )


def align_prepared_scene(
    manifest_path: Path,
    grid: CompositeGrid,
) -> AlignedResourceSatScene:
    manifest = _read_manifest(manifest_path)
    source_id = str(manifest.get("source_id") or "")
    profile = profile_for_source(source_id)
    acquisition_at = _required_datetime(manifest.get("acquisition_datetime"), manifest_path)
    analytic_path = _resolve_manifest_path(manifest_path, manifest, "analytic")
    mask_path = _resolve_manifest_path(manifest_path, manifest, "mask")
    with rasterio.open(analytic_path) as analytic_source, rasterio.open(mask_path) as mask_source:
        if analytic_source.count != len(profile.band_order):
            raise ValueError(
                f"{manifest_path}: expected {len(profile.band_order)} analytic bands, "
                f"got {analytic_source.count}"
            )
        aligned_analytic = np.zeros(
            (analytic_source.count, grid.height, grid.width),
            dtype="uint16",
        )
        aligned_mask = np.zeros((grid.height, grid.width), dtype="uint8")
        for band_index in range(1, analytic_source.count + 1):
            reproject(
                source=rasterio.band(analytic_source, band_index),
                destination=aligned_analytic[band_index - 1],
                src_transform=analytic_source.transform,
                src_crs=analytic_source.crs,
                src_nodata=0,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                dst_nodata=0,
                resampling=Resampling.bilinear,
            )
        reproject(
            source=rasterio.band(mask_source, 1),
            destination=aligned_mask,
            src_transform=mask_source.transform,
            src_crs=mask_source.crs,
            src_nodata=0,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )
    return AlignedResourceSatScene(
        scene_id=str(manifest.get("product_id") or manifest_path.stem),
        acquisition_at=acquisition_at,
        analytic=aligned_analytic,
        mask=aligned_mask,
    )


def build_best_available_composite(
    scenes: list[AlignedResourceSatScene],
    *,
    aoi_mask: NDArray[np.bool_],
) -> dict[str, Any]:
    if not scenes:
        raise ValueError("at least one ResourceSat scene is required")
    sorted_scenes = sorted(scenes, key=lambda scene: scene.acquisition_at)
    band_count, height, width = sorted_scenes[0].analytic.shape
    if aoi_mask.shape != (height, width):
        raise ValueError("AOI mask shape must match aligned scenes")
    output = np.zeros((band_count, height, width), dtype="uint16")
    output_mask = np.zeros((height, width), dtype="uint8")
    source_scene_index = np.full((height, width), -1, dtype="int16")
    has_any = np.zeros((height, width), dtype=bool)

    for scene_index, scene in enumerate(sorted_scenes):
        if scene.analytic.shape != output.shape or scene.mask.shape != output_mask.shape:
            raise ValueError(f"{scene.scene_id}: shape does not match composite grid")
        covered = (scene.mask != 0) & aoi_mask
        valid = np.isin(scene.mask, np.array(RESOURCESAT_VALID_MASK_CLASSES)) & aoi_mask
        fallback_take = covered & ~has_any
        take = fallback_take | valid
        output[:, take] = scene.analytic[:, take]
        output_mask[take] = scene.mask[take]
        source_scene_index[take] = scene_index
        has_any |= covered

    output_mask[~aoi_mask] = 0
    output[:, ~aoi_mask] = 0
    denominator = int(aoi_mask.sum())
    covered_final = (output_mask != 0) & aoi_mask
    usable_final = np.isin(output_mask, np.array(RESOURCESAT_VALID_MASK_CLASSES)) & aoi_mask
    cloud_masked = np.isin(output_mask, np.array([2, 3], dtype="uint8")) & aoi_mask
    metrics = {
        "aoi_pixel_count": denominator,
        "covered_pixel_count": int(covered_final.sum()),
        "usable_pixel_count": int(usable_final.sum()),
        "cloud_masked_pixel_count": int(cloud_masked.sum()),
        "coverage_percent": _percent(covered_final.sum(), denominator),
        "usable_pixel_percent": _percent(usable_final.sum(), denominator),
        "cloud_masked_percent": _percent(cloud_masked.sum(), denominator),
        "contributing_scenes": [
            {
                "id": scene.scene_id,
                "acquisition_datetime": scene.acquisition_at.isoformat(),
            }
            for scene in sorted_scenes
        ],
    }
    return {
        "analytic": output,
        "mask": output_mask,
        "source_scene_index": source_scene_index,
        "metrics": metrics,
        "scenes": sorted_scenes,
    }


def build_resource_sat_composite(
    *,
    manifest_paths: list[Path],
    aoi: AoiRecord,
    output_root: Path,
    settings: Settings,
    dry_run: bool = False,
) -> ResourceSatCompositeBuildResult:
    validate_resourcesat_runtime_roots(settings, dry_run=dry_run)
    _validate_output_root(output_root, settings=settings, dry_run=dry_run)
    manifests = [_read_manifest(path) for path in manifest_paths]
    source_ids = {str(manifest.get("source_id") or "") for manifest in manifests}
    if len(source_ids) != 1:
        raise ValueError(f"ResourceSat composite requires one source_id, got {source_ids}")
    for path, manifest in zip(manifest_paths, manifests, strict=True):
        _validate_prepare_manifest_for_composite(path, manifest, aoi)
    profile = profile_for_source(next(iter(source_ids)))
    policy = composite_policy(profile, settings)
    grid = grid_from_aoi(aoi, profile, settings)
    mask = aoi_mask_for_grid(aoi, grid)
    aligned_scenes = [align_prepared_scene(path, grid) for path in manifest_paths]
    composite = build_best_available_composite(aligned_scenes, aoi_mask=mask)
    composite_datetime = max(scene.acquisition_at for scene in composite["scenes"])
    composite_date = composite_datetime.date().isoformat()
    output_dir = output_root / profile.source_id / "composite" / aoi.aoi_id / composite_date
    output_dir.mkdir(parents=True, exist_ok=True)
    analytic_path = output_dir / "analytic.tif"
    mask_path = output_dir / "mask.tif"
    tags = {
        "AKASHA_SOURCE_ID": profile.source_id,
        "AKASHA_AOI_ID": aoi.aoi_id,
        "AKASHA_OUTPUT_KIND": COMPOSITE_OUTPUT_KIND,
        "AKASHA_MASK_METHOD": RESOURCESAT_MASK_METHOD,
        "AKASHA_METRICS_PROVISIONAL": "true",
        "AREA_OR_POINT": "Area",
    }
    write_cog_file(
        composite["analytic"],
        analytic_path,
        transform=grid.transform,
        crs=grid.crs,
        nodata=0,
        tags=tags,
        band_descriptions=tuple(
            f"{band_name} {role}"
            for role, band_name in profile.band_roles.items()
        ),
        overview_resampling="bilinear",
    )
    write_cog_file(
        composite["mask"],
        mask_path,
        transform=grid.transform,
        crs=grid.crs,
        nodata=0,
        tags=tags | {"AKASHA_ASSET_KIND": "resourcesat-composite-mask"},
        band_descriptions=("mask",),
        overview_resampling="nearest",
    )
    analytic_checksum = file_sha256(analytic_path)
    mask_checksum = file_sha256(mask_path)
    manifest = build_composite_manifest(
        profile=profile,
        aoi=aoi,
        grid=grid,
        composite_datetime=composite_datetime,
        analytic_path=analytic_path,
        mask_path=mask_path,
        analytic_checksum=analytic_checksum,
        mask_checksum=mask_checksum,
        metrics=composite["metrics"],
        policy=policy,
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    verify = verify_resource_sat_composite(manifest_path, settings=settings)
    if not verify.ok:
        raise ValueError("; ".join(verify.problems))
    return ResourceSatCompositeBuildResult(
        output_dir=output_dir,
        analytic_cog=analytic_path,
        mask_cog=mask_path,
        manifest_path=manifest_path,
        manifest=manifest,
        metrics=composite["metrics"],
    )


def build_composite_manifest(
    *,
    profile: ResourceSatProfile,
    aoi: AoiRecord,
    grid: CompositeGrid,
    composite_datetime: datetime,
    analytic_path: Path,
    mask_path: Path,
    analytic_checksum: str,
    mask_checksum: str,
    metrics: dict[str, Any],
    policy: ResourceSatCompositePolicy,
) -> dict[str, Any]:
    composite_date = composite_datetime.date().isoformat()
    warnings = list(policy.warnings)
    if metrics["coverage_percent"] < policy.min_coverage_percent:
        warnings.append("coverage_below_threshold")
    return {
        "schema_version": "resourcesat-composite-manifest-v1",
        "output_kind": COMPOSITE_OUTPUT_KIND,
        "composite": True,
        "source_id": profile.source_id,
        "collection": profile.collection_id,
        "aoi_id": aoi.aoi_id,
        "composite_date": composite_date,
        "composite_datetime": composite_datetime.isoformat(),
        "bbox": aoi.bbox,
        "geometry": aoi.geometry,
        "grid": {
            "crs": grid.crs,
            "bounds": list(grid.bounds),
            "resolution": grid.resolution,
            "width": grid.width,
            "height": grid.height,
            "transform": list(grid.transform)[:6],
        },
        "band_order": list(profile.band_order),
        "band_role_mapping": dict(profile.band_roles),
        "mask_method": RESOURCESAT_MASK_METHOD,
        "classification_classes": [
            {
                "value": item.value,
                "label": item.label,
                "description": item.description,
                "valid_for_analytics": item.valid_for_analytics,
            }
            for item in RESOURCESAT_MASK_CLASSES
        ],
        "akasha:metrics_provisional": True,
        "coverage_policy": {
            "min_coverage_percent": policy.min_coverage_percent,
        },
        "warnings": warnings,
        "metrics": metrics,
        "outputs": {
            "analytic": {
                "path": str(analytic_path),
                "checksum_sha256": analytic_checksum,
                "size_bytes": analytic_path.stat().st_size,
                "dtype": "uint16",
                "nodata": 0,
                "band_count": len(profile.band_order),
            },
            "mask": {
                "path": str(mask_path),
                "checksum_sha256": mask_checksum,
                "size_bytes": mask_path.stat().st_size,
                "dtype": "uint8",
                "nodata": 0,
                "band_count": 1,
            },
        },
        "properties": {
            "akasha:composite": True,
            "akasha:output_kind": COMPOSITE_OUTPUT_KIND,
            "akasha:mask_method": RESOURCESAT_MASK_METHOD,
            "akasha:metrics_provisional": True,
            "akasha:composite_grid_crs": grid.crs,
            "akasha:composite_resolution_m": grid.resolution,
            "akasha:contributing_scenes": metrics["contributing_scenes"],
        },
    }


def verify_resource_sat_composite(
    manifest_path: Path,
    *,
    settings: Settings,
) -> ResourceSatCompositeVerifyResult:
    checks: list[str] = []
    problems: list[str] = []
    try:
        manifest = _read_manifest(manifest_path)
        _verify_manifest_fields(manifest, checks, problems)
        profile = profile_for_source(str(manifest.get("source_id") or ""))
        policy = composite_policy(profile, settings)
        analytic_path = _resolve_manifest_path(manifest_path, manifest, "analytic")
        mask_path = _resolve_manifest_path(manifest_path, manifest, "mask")
        recomputed_coverage: float | None = None
        recomputed_usable: float | None = None
        with rasterio.open(analytic_path) as analytic, rasterio.open(mask_path) as mask:
            checks.append("outputs_open")
            if analytic.count != len(profile.band_order):
                problems.append("analytic band count does not match source profile")
            if mask.count != 1:
                problems.append("mask COG must have one band")
            if (analytic.width, analytic.height) != (mask.width, mask.height):
                problems.append("analytic/mask dimensions differ")
            if str(analytic.crs) != str(mask.crs):
                problems.append("analytic/mask CRS differ")
            expected_resolution = profile.processing_resolution_m(settings)
            actual_resolution = abs(float(analytic.transform.a))
            if abs(actual_resolution - expected_resolution) > profile.native_resolution_tolerance_m:
                problems.append(
                    f"composite resolution {actual_resolution} differs from "
                    f"expected {expected_resolution}"
                )
            mask_array = mask.read(1)
            mask_values = set(np.unique(mask_array).astype(int).tolist())
            allowed_mask_values = {item.value for item in RESOURCESAT_MASK_CLASSES}
            if not mask_values <= allowed_mask_values:
                problems.append(f"unexpected mask classes: {sorted(mask_values)}")
            aoi_mask = _aoi_mask_from_manifest(manifest, mask)
            covered = (mask_array != 0) & aoi_mask
            usable = np.isin(mask_array, np.array(RESOURCESAT_VALID_MASK_CLASSES)) & aoi_mask
            denominator = int(aoi_mask.sum())
            recomputed_coverage = _percent(covered.sum(), denominator)
            recomputed_usable = _percent(usable.sum(), denominator)
        for path, label in ((analytic_path, "analytic"), (mask_path, "mask")):
            valid, errors, warnings = validate_cog(path)
            if not valid:
                problems.append(f"{label} COG failed validation: {errors or warnings}")
        checks.append("cogs_valid")
        metrics = manifest.get("metrics")
        if isinstance(metrics, dict):
            coverage = float(metrics.get("coverage_percent") or 0.0)
            usable = float(metrics.get("usable_pixel_percent") or 0.0)
            if recomputed_coverage is not None and abs(coverage - recomputed_coverage) > 0.01:
                problems.append(
                    f"manifest coverage {coverage:.2f}% does not match "
                    f"mask coverage {recomputed_coverage:.2f}%"
                )
            if recomputed_usable is not None and abs(usable - recomputed_usable) > 0.01:
                problems.append(
                    f"manifest usable coverage {usable:.2f}% does not match "
                    f"mask usable coverage {recomputed_usable:.2f}%"
                )
            coverage = recomputed_coverage if recomputed_coverage is not None else coverage
            if coverage < policy.min_coverage_percent:
                problems.append(
                    f"coverage {coverage:.2f}% below threshold "
                    f"{policy.min_coverage_percent:.2f}%"
                )
        else:
            problems.append("metrics missing or invalid")
    except Exception as exc:
        problems.append(str(exc))
    return ResourceSatCompositeVerifyResult(ok=not problems, checks=checks, problems=problems)


def _validate_prepare_manifest_for_composite(
    manifest_path: Path,
    manifest: dict[str, Any],
    aoi: AoiRecord,
) -> None:
    required = ("source_id", "collection", "product_id", "aoi_id", "outputs")
    missing = [key for key in required if not manifest.get(key)]
    if missing:
        raise ValueError(
            f"{manifest_path}: prepare manifest missing composite provenance: "
            f"{', '.join(missing)}"
        )
    _required_datetime(manifest.get("acquisition_datetime"), manifest_path)
    if manifest["aoi_id"] != aoi.aoi_id:
        raise ValueError(
            f"{manifest_path}: prepare manifest AOI {manifest['aoi_id']} does not match "
            f"requested AOI {aoi.aoi_id}"
        )


def _verify_manifest_fields(
    manifest: dict[str, Any],
    checks: list[str],
    problems: list[str],
) -> None:
    required = {
        "schema_version",
        "output_kind",
        "source_id",
        "collection",
        "aoi_id",
        "composite",
        "composite_datetime",
        "bbox",
        "geometry",
        "grid",
        "band_order",
        "band_role_mapping",
        "mask_method",
        "classification_classes",
        "outputs",
        "metrics",
    }
    missing = sorted(key for key in required if key not in manifest)
    if missing:
        problems.append(f"composite manifest missing fields: {', '.join(missing)}")
    if manifest.get("composite") is not True:
        problems.append("composite manifest must set composite=true")
    if manifest.get("output_kind") != COMPOSITE_OUTPUT_KIND:
        problems.append("composite manifest has wrong output_kind")
    if manifest.get("mask_method") != RESOURCESAT_MASK_METHOD:
        problems.append("composite manifest has wrong mask method")
    if manifest.get("akasha:metrics_provisional") is not True:
        problems.append("composite manifest must mark metrics provisional")
    properties = manifest.get("properties")
    if not isinstance(properties, dict):
        problems.append("composite properties missing or invalid")
    else:
        if properties.get("akasha:composite") is not True:
            problems.append("composite properties must set akasha:composite=true")
        _verify_contributing_scenes(
            properties.get("akasha:contributing_scenes"),
            "properties.akasha:contributing_scenes",
            problems,
        )
    metrics = manifest.get("metrics")
    if isinstance(metrics, dict):
        _verify_contributing_scenes(
            metrics.get("contributing_scenes"),
            "metrics.contributing_scenes",
            problems,
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        problems.append("outputs missing or invalid")
    else:
        for key in ("analytic", "mask"):
            output = outputs.get(key)
            if not isinstance(output, dict):
                problems.append(f"{key} output missing")
                continue
            for required_output_key in ("path", "checksum_sha256", "size_bytes", "band_count"):
                if required_output_key not in output:
                    problems.append(f"{key} output missing {required_output_key}")
    _required_datetime(manifest.get("composite_datetime"), Path("<manifest>"))
    checks.append("manifest_fields")


def _verify_contributing_scenes(
    value: Any,
    label: str,
    problems: list[str],
) -> None:
    if not isinstance(value, list) or not value:
        problems.append(f"{label} must be a non-empty list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            problems.append(f"{label}[{index}] must be an object")
            continue
        if not item.get("id"):
            problems.append(f"{label}[{index}] missing id")
        if not item.get("acquisition_datetime"):
            problems.append(f"{label}[{index}] missing acquisition_datetime")


def _aoi_mask_from_manifest(
    manifest: dict[str, Any],
    mask: rasterio.io.DatasetReader,
) -> NDArray[np.bool_]:
    geometry = manifest.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("composite manifest geometry missing or invalid")
    projected = transform_geom("EPSG:4326", str(mask.crs), geometry)
    return geometry_mask(
        [projected],
        out_shape=(mask.height, mask.width),
        transform=mask.transform,
        invert=True,
    )


def _required_datetime(value: Any, source: Path) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source}: acquisition datetime is required")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _resolve_manifest_path(
    manifest_path: Path,
    manifest: dict[str, Any],
    output_key: str,
) -> Path:
    output = manifest.get("outputs")
    if not isinstance(output, dict) or not isinstance(output.get(output_key), dict):
        raise ValueError(f"{manifest_path}: missing {output_key} output")
    path = Path(str(output[output_key].get("path") or ""))
    return path if path.is_absolute() else manifest_path.parent / path


def _read_manifest(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"manifest must be an object: {path}")
    return parsed


def _utm_crs_for_lon_lat(*, lon: float, lat: float) -> str:
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(60, max(1, zone))
    epsg_base = 32600 if lat >= 0 else 32700
    return f"EPSG:{epsg_base + zone}"


def _percent(numerator: int | np.integer, denominator: int) -> float:
    return (float(numerator) / denominator) * 100.0 if denominator else 0.0


def _validate_output_root(
    output_root: Path,
    *,
    settings: Settings,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    resolved = output_root.resolve(strict=False)
    approved_roots = [Path("/srv/akasha")]
    if settings.resourcesat_approved_data_root is not None:
        approved_roots.append(Path(settings.resourcesat_approved_data_root))
    if not any(resolved.is_relative_to(root.resolve(strict=False)) for root in approved_roots):
        approved = ", ".join(str(path) for path in approved_roots)
        raise ValueError(f"composite output root must be under approved roots ({approved})")
