from __future__ import annotations

import json
import re
import shutil
import stat
import zipfile
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.windows import Window

from akasha.config import Settings, validate_resourcesat_runtime_roots
from akasha.processing.cog import translate_cog_file
from akasha.processing.resourcesat import (
    GREEN,
    NIR,
    RED,
    RESOURCESAT_MASK_CLASSES,
    RESOURCESAT_MASK_METHOD,
    RESOURCESAT_REFLECTANCE_OFFSET,
    RESOURCESAT_REFLECTANCE_SCALE,
    SWIR1,
    ResourceSatProfile,
    profile_for_source,
    reflectance_from_dn,
)
from akasha.providers.contracts import ProviderErrorCategory
from akasha.storage.object_store import file_sha256


@dataclass(frozen=True, slots=True)
class SelectedResourceSatProduct:
    source_id: str
    product_id: str
    package_path: Path
    acquisition_at: datetime | None = None
    aoi_id: str | None = None
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceSatBandMetadata:
    band_name: str
    role: str
    source_path: Path
    path: str | None
    row: str | None
    acquisition_at: datetime | None
    valid_range: tuple[int, int] | None
    background_values: tuple[int, ...]
    reflectance_scale: float
    reflectance_offset: float


@dataclass(frozen=True, slots=True)
class PreparedResourceSatScene:
    source_id: str
    collection_id: str
    product_id: str
    acquisition_at: datetime | None
    analytic_path: Path
    mask_path: Path
    analytic_checksum_sha256: str
    mask_checksum_sha256: str
    band_metadata: dict[str, ResourceSatBandMetadata]
    manifest: dict[str, Any]


class ResourceSatPrepareError(RuntimeError):
    def __init__(
        self,
        category: ProviderErrorCategory,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.metadata = metadata or {}


def prepare_resourcesat_product(
    product: SelectedResourceSatProduct,
    settings: Settings,
    *,
    dry_run: bool = False,
    max_members: int = 2_000,
    max_expanded_bytes: int = 20 * 1024 * 1024 * 1024,
) -> PreparedResourceSatScene:
    validate_resourcesat_runtime_roots(settings, dry_run=dry_run)
    profile = profile_for_source(product.source_id)
    product_component = _safe_component(product.product_id)
    work_root = (
        Path(settings.scratch_dir)
        / "resourcesat-prepare"
        / product.source_id
        / product_component
    )
    extract_root = work_root / "extract"
    prepared_root = work_root / "prepared"
    if work_root.exists():
        shutil.rmtree(work_root)
    prepared_root.mkdir(parents=True, exist_ok=True)

    try:
        safe_extract_product(
            product.package_path,
            extract_root,
            max_members=max_members,
            max_expanded_bytes=max_expanded_bytes,
        )
        band_files = discover_band_files(extract_root, profile)
        band_metadata = parse_band_metadata(
            extract_root=extract_root,
            profile=profile,
            band_files=band_files,
        )
        analytic_path = prepared_root / "analytic.cog.tif"
        mask_path = prepared_root / "mask.cog.tif"
        common_tags = {
            "AKASHA_SOURCE_ID": product.source_id,
            "AKASHA_COLLECTION_ID": profile.collection_id,
            "AKASHA_PRODUCT_ID": product.product_id,
            "AKASHA_PROCESSING_PROFILE_VERSION": profile.processing_profile_version,
            "AKASHA_REFLECTANCE_SCALE": str(RESOURCESAT_REFLECTANCE_SCALE),
            "AKASHA_REFLECTANCE_OFFSET": str(RESOURCESAT_REFLECTANCE_OFFSET),
            "AKASHA_MASK_METHOD": RESOURCESAT_MASK_METHOD,
            "AREA_OR_POINT": "Area",
        }
        crs, bbox = write_prepared_scene_cogs(
            profile=profile,
            band_files=band_files,
            analytic_path=analytic_path,
            mask_path=mask_path,
            tags=common_tags,
        )
        analytic_checksum = file_sha256(analytic_path)
        mask_checksum = file_sha256(mask_path)
        acquisition_at = product.acquisition_at or _first_acquisition_at(band_metadata)
        manifest = build_prepare_manifest(
            product=product,
            profile=profile,
            band_metadata=band_metadata,
            analytic_path=analytic_path,
            mask_path=mask_path,
            analytic_checksum=analytic_checksum,
            mask_checksum=mask_checksum,
            crs=crs,
            bbox=product.bbox or bbox,
            geometry=product.geometry,
        )
        return PreparedResourceSatScene(
            source_id=product.source_id,
            collection_id=profile.collection_id,
            product_id=product.product_id,
            acquisition_at=acquisition_at,
            analytic_path=analytic_path,
            mask_path=mask_path,
            analytic_checksum_sha256=analytic_checksum,
            mask_checksum_sha256=mask_checksum,
            band_metadata=band_metadata,
            manifest=manifest,
        )
    except ResourceSatPrepareError:
        raise
    except Exception as exc:
        raise ResourceSatPrepareError(
            ProviderErrorCategory.PREPARE_FAILED,
            f"failed to prepare ResourceSat product: {product.product_id}",
            metadata={"product_id": product.product_id, "source_id": product.source_id},
        ) from exc


def safe_extract_product(
    zip_path: Path,
    extract_root: Path,
    *,
    max_members: int = 2_000,
    max_expanded_bytes: int = 20 * 1024 * 1024 * 1024,
) -> Path:
    extract_root = extract_root.resolve(strict=False)
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            if len(members) > max_members:
                raise ResourceSatPrepareError(
                    ProviderErrorCategory.INVALID_PRODUCT,
                    "Bhoonidhi ZIP has too many members.",
                    metadata={"member_count": len(members), "max_members": max_members},
                )
            expanded_bytes = sum(member.file_size for member in members)
            if expanded_bytes > max_expanded_bytes:
                raise ResourceSatPrepareError(
                    ProviderErrorCategory.INVALID_PRODUCT,
                    "Bhoonidhi ZIP expanded size exceeds the configured limit.",
                    metadata={
                        "expanded_bytes": expanded_bytes,
                        "max_expanded_bytes": max_expanded_bytes,
                    },
                )
            member_targets = {
                member: _safe_member_target(extract_root, member) for member in members
            }
            extracted_bytes = 0
            for member, target_path in member_targets.items():
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with archive.open(member) as source, target_path.open("wb") as target:
                        extracted_bytes = _copy_member_with_limit(
                            source,
                            target,
                            extracted_bytes=extracted_bytes,
                            max_expanded_bytes=max_expanded_bytes,
                        )
                except zipfile.BadZipFile as exc:
                    raise ResourceSatPrepareError(
                        ProviderErrorCategory.INVALID_PRODUCT,
                        "corrupt Bhoonidhi ZIP member.",
                        metadata={"member": member.filename},
                    ) from exc
    except zipfile.BadZipFile as exc:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise ResourceSatPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "invalid Bhoonidhi ZIP package.",
        ) from exc
    except Exception:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise
    return extract_root


def discover_band_files(
    extract_root: Path,
    profile: ResourceSatProfile,
) -> dict[str, Path]:
    candidates = sorted(
        path
        for path in extract_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )
    discovered: dict[str, Path] = {}
    for band_name in profile.band_order:
        band_key = _normalize_band_token(band_name)
        for candidate in candidates:
            if band_key in _normalize_band_token(candidate.stem):
                discovered[band_name] = candidate
                break
    missing = [band_name for band_name in profile.band_order if band_name not in discovered]
    if missing:
        raise ResourceSatPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "ResourceSat product is missing required bands.",
            metadata={"source_id": profile.source_id, "missing_bands": missing},
        )
    return discovered


def parse_band_metadata(
    *,
    extract_root: Path,
    profile: ResourceSatProfile,
    band_files: Mapping[str, Path],
) -> dict[str, ResourceSatBandMetadata]:
    metadata = _read_product_metadata(extract_root)
    bands_metadata = metadata.get("bands") if isinstance(metadata.get("bands"), Mapping) else {}
    result: dict[str, ResourceSatBandMetadata] = {}
    roles_by_band = {band_name: role for role, band_name in profile.band_roles.items()}
    for band_name, source_path in band_files.items():
        role = roles_by_band[band_name]
        band_metadata = _mapping(
            bands_metadata.get(band_name)
            or bands_metadata.get(role)
            or bands_metadata.get(role.lower())
            or {}
        )
        result[band_name] = ResourceSatBandMetadata(
            band_name=band_name,
            role=role,
            source_path=source_path,
            path=_optional_string(band_metadata.get("path") or metadata.get("path")),
            row=_optional_string(band_metadata.get("row") or metadata.get("row")),
            acquisition_at=_metadata_datetime(band_metadata) or _metadata_datetime(metadata),
            valid_range=_optional_int_pair(
                band_metadata.get("valid_range") or metadata.get("valid_range")
            ),
            background_values=_optional_int_tuple(
                band_metadata.get("background_values") or metadata.get("background_values")
            ),
            reflectance_scale=float(
                band_metadata.get("reflectance_scale")
                or metadata.get("reflectance_scale")
                or RESOURCESAT_REFLECTANCE_SCALE
            ),
            reflectance_offset=float(
                band_metadata.get("reflectance_offset")
                or metadata.get("reflectance_offset")
                or RESOURCESAT_REFLECTANCE_OFFSET
            ),
        )
    return result


def build_threshold_mask(
    profile: ResourceSatProfile,
    analytic_bands: NDArray[np.integer],
) -> NDArray[np.uint8]:
    if analytic_bands.shape[0] != len(profile.band_order):
        raise ValueError(
            f"expected {len(profile.band_order)} ResourceSat bands for {profile.source_id}, "
            f"got {analytic_bands.shape[0]}"
        )
    bands_by_role = _bands_by_role(profile, analytic_bands)
    green = reflectance_from_dn(bands_by_role[GREEN])
    red = reflectance_from_dn(bands_by_role[RED])
    nir = reflectance_from_dn(bands_by_role[NIR])
    gap = np.any(analytic_bands == 0, axis=0)
    mask = np.ones(analytic_bands.shape[1:], dtype="uint8")
    mask[gap] = 0
    denominator = green + nir
    ndwi = np.full(green.shape, np.nan, dtype="float32")
    valid_denominator = np.abs(denominator) > 1e-6
    ndwi[valid_denominator] = (
        (green[valid_denominator] - nir[valid_denominator])
        / denominator[valid_denominator]
    )
    water = (ndwi >= 0.20) & (nir <= 0.20) & ~gap
    brightness = (green + red + nir) / 3
    if SWIR1 in bands_by_role:
        swir = reflectance_from_dn(bands_by_role[SWIR1])
        cloud = (brightness >= 0.32) & (swir >= 0.20) & ~gap
        shadow = (
            (nir <= 0.08)
            & (swir <= 0.08)
            & (red <= 0.08)
            & ~gap
            & ~water
        )
    else:
        ndvi_denominator = nir + red
        ndvi = np.full(green.shape, np.nan, dtype="float32")
        valid_ndvi = np.abs(ndvi_denominator) > 1e-6
        ndvi[valid_ndvi] = (
            (nir[valid_ndvi] - red[valid_ndvi]) / ndvi_denominator[valid_ndvi]
        )
        cloud = (brightness >= 0.32) & (ndvi <= 0.20) & ~gap & ~water
        shadow = (
            (green <= 0.08)
            & (red <= 0.08)
            & (nir <= 0.08)
            & ~gap
            & ~water
            & ~cloud
        )
    mask[cloud] = 2
    mask[shadow] = 3
    mask[water] = 4
    return mask


def write_prepared_scene_cogs(
    *,
    profile: ResourceSatProfile,
    band_files: Mapping[str, Path],
    analytic_path: Path,
    mask_path: Path,
    tags: Mapping[str, str],
) -> tuple[str, list[float]]:
    source_analytic_path = analytic_path.with_name("analytic.source.tif")
    source_mask_path = mask_path.with_name("mask.source.tif")
    with ExitStack() as stack:
        datasets = [
            stack.enter_context(rasterio.open(band_files[band_name]))
            for band_name in profile.band_order
        ]
        reference = datasets[0]
        _validate_aligned_bands(profile, datasets)
        block_x_size = _tile_size(reference.width)
        block_y_size = _tile_size(reference.height)
        base_profile = {
            "driver": "GTiff",
            "width": reference.width,
            "height": reference.height,
            "crs": reference.crs,
            "transform": reference.transform,
            "tiled": True,
            "blockxsize": block_x_size,
            "blockysize": block_y_size,
        }
        with rasterio.open(
            source_analytic_path,
            "w",
            **base_profile,
            count=len(profile.band_order),
            dtype="uint16",
            nodata=0,
        ) as analytic, rasterio.open(
            source_mask_path,
            "w",
            **base_profile,
            count=1,
            dtype="uint8",
            nodata=0,
        ) as mask:
            analytic.update_tags(**dict(tags), AKASHA_ASSET_KIND="resourcesat-analytic")
            mask.update_tags(**dict(tags), AKASHA_ASSET_KIND="resourcesat-mask")
            for band_index, description in enumerate(_band_descriptions(profile), start=1):
                analytic.set_band_description(band_index, description)
            mask.set_band_description(1, "mask")

            for window in _bounded_windows(reference.width, reference.height):
                window_bands: list[NDArray[np.uint16]] = []
                for band_index, dataset in enumerate(datasets, start=1):
                    values = dataset.read(1, window=window).astype("uint16", copy=False)
                    analytic.write(values, band_index, window=window)
                    window_bands.append(values)
                window_stack = np.stack(window_bands)
                mask.write(build_threshold_mask(profile, window_stack), 1, window=window)

        crs = str(reference.crs)
        bounds = reference.bounds

    translate_cog_file(source_analytic_path, analytic_path, overview_resampling="bilinear")
    translate_cog_file(source_mask_path, mask_path, overview_resampling="nearest")
    source_analytic_path.unlink(missing_ok=True)
    source_mask_path.unlink(missing_ok=True)
    return (
        crs,
        [
            float(bounds.left),
            float(bounds.bottom),
            float(bounds.right),
            float(bounds.top),
        ],
    )


def build_prepare_manifest(
    *,
    product: SelectedResourceSatProduct,
    profile: ResourceSatProfile,
    band_metadata: Mapping[str, ResourceSatBandMetadata],
    analytic_path: Path,
    mask_path: Path,
    analytic_checksum: str,
    mask_checksum: str,
    crs: str,
    bbox: list[float],
    geometry: dict[str, Any] | None,
) -> dict[str, Any]:
    acquisition_at = product.acquisition_at or _first_acquisition_at(band_metadata)
    first_metadata = next(iter(band_metadata.values()), None)
    return {
        "schema_version": "resourcesat-prepare-manifest-v1",
        "source_id": product.source_id,
        "collection": profile.collection_id,
        "product_id": product.product_id,
        "aoi_id": product.aoi_id,
        "acquisition_datetime": acquisition_at.isoformat() if acquisition_at else None,
        "path": first_metadata.path if first_metadata else None,
        "row": first_metadata.row if first_metadata else None,
        "bbox": bbox,
        "geometry": geometry,
        "crs": crs,
        "band_order": list(profile.band_order),
        "band_role_mapping": dict(profile.band_roles),
        "reflectance_scale": RESOURCESAT_REFLECTANCE_SCALE,
        "reflectance_offset": RESOURCESAT_REFLECTANCE_OFFSET,
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
        "bands": {
            band_name: {
                "role": item.role,
                "source_path": str(item.source_path),
                "path": item.path,
                "row": item.row,
                "acquisition_datetime": (
                    item.acquisition_at.isoformat() if item.acquisition_at else None
                ),
                "valid_range": list(item.valid_range) if item.valid_range else None,
                "background_values": list(item.background_values),
                "reflectance_scale": item.reflectance_scale,
                "reflectance_offset": item.reflectance_offset,
            }
            for band_name, item in band_metadata.items()
        },
    }


def _safe_member_target(extract_root: Path, member: zipfile.ZipInfo) -> Path:
    member_name = member.filename
    normalized = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(member_name)
    mode = member.external_attr >> 16
    if (
        not normalized
        or posix_path.is_absolute()
        or windows_path.drive
        or normalized.startswith("//")
        or any(part in {"", ".", ".."} for part in posix_path.parts)
        or stat.S_ISLNK(mode)
    ):
        raise ResourceSatPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "unsafe Bhoonidhi ZIP member path.",
            metadata={"member": member_name},
        )
    target_path = (extract_root / Path(*posix_path.parts)).resolve(strict=False)
    if not target_path.is_relative_to(extract_root):
        raise ResourceSatPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "Bhoonidhi ZIP member escapes extraction root.",
            metadata={"member": member_name},
        )
    return target_path


def _validate_aligned_bands(
    profile: ResourceSatProfile,
    datasets: list[rasterio.io.DatasetReader],
) -> None:
    reference = datasets[0]
    for band_name, dataset in zip(profile.band_order[1:], datasets[1:], strict=True):
        if (
            dataset.width != reference.width
            or dataset.height != reference.height
            or dataset.transform != reference.transform
            or dataset.crs != reference.crs
        ):
            raise ResourceSatPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "ResourceSat band grids are not aligned.",
                metadata={"band_name": band_name},
            )


def _tile_size(size: int) -> int:
    return 16 if size <= 16 else 256


def _bounded_windows(
    width: int,
    height: int,
    *,
    max_window_size: int = 512,
) -> list[Window]:
    return [
        Window(
            col_off,
            row_off,
            min(max_window_size, width - col_off),
            min(max_window_size, height - row_off),
        )
        for row_off in range(0, height, max_window_size)
        for col_off in range(0, width, max_window_size)
    ]


def _copy_member_with_limit(
    source: Any,
    target: Any,
    *,
    extracted_bytes: int,
    max_expanded_bytes: int,
) -> int:
    total = extracted_bytes
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        total += len(chunk)
        if total > max_expanded_bytes:
            raise ResourceSatPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "Bhoonidhi ZIP expanded size exceeds the configured limit.",
                metadata={
                    "expanded_bytes": total,
                    "max_expanded_bytes": max_expanded_bytes,
                },
            )
        target.write(chunk)
    return total


def _bands_by_role(
    profile: ResourceSatProfile,
    analytic_bands: NDArray[np.integer],
) -> dict[str, NDArray[np.integer]]:
    band_index = {band_name: index for index, band_name in enumerate(profile.band_order)}
    return {
        role: analytic_bands[band_index[band_name]]
        for role, band_name in profile.band_roles.items()
    }


def _band_descriptions(profile: ResourceSatProfile) -> tuple[str, ...]:
    role_by_band = {band_name: role for role, band_name in profile.band_roles.items()}
    return tuple(f"{band_name} {role_by_band[band_name]}" for band_name in profile.band_order)


def _read_product_metadata(extract_root: Path) -> dict[str, Any]:
    for path in sorted(extract_root.rglob("*.json")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    for path in sorted(extract_root.rglob("*.xml")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        return {
            "path": _regex_first(text, r"<(?:path|Path)>([^<]+)</"),
            "row": _regex_first(text, r"<(?:row|Row)>([^<]+)</"),
            "acquisition_datetime": _regex_first(
                text,
                r"<(?:acquisition_datetime|acquisitionDate|date|Date)>([^<]+)</",
            ),
        }
    return {}


def _metadata_datetime(metadata: Mapping[str, Any]) -> datetime | None:
    for key in ("acquisition_datetime", "acquisition_at", "datetime", "date", "acquisitionDate"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return _parse_datetime(value)
    return None


def _parse_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _first_acquisition_at(metadata: Mapping[str, ResourceSatBandMetadata]) -> datetime | None:
    for item in metadata.values():
        if item.acquisition_at is not None:
            return item.acquisition_at
    return None


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "product"


def _normalize_band_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _optional_int_pair(value: Any) -> tuple[int, int] | None:
    values = _optional_int_tuple(value)
    if len(values) >= 2:
        return (values[0], values[1])
    return None


def _optional_int_tuple(value: Any) -> tuple[int, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        parts = re.split(r"[,;\s]+", value.strip())
    elif isinstance(value, list | tuple):
        parts = value
    else:
        parts = (value,)
    result: list[int] = []
    for item in parts:
        if item in {None, ""}:
            continue
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(result)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _regex_first(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None
