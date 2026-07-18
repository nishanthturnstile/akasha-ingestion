from __future__ import annotations

import re
import shutil
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

from akasha.config import Settings, validate_resourcesat_runtime_roots
from akasha.processing.cog import translate_cog_file, validate_cog
from akasha.processing.resourcesat_prepare import safe_extract_product
from akasha.processing.sar_comparability import normalize_eos04_comparison_metadata
from akasha.providers.contracts import ProviderErrorCategory
from akasha.storage.object_store import file_sha256

EOS04_SOURCE_ID = "eos-04-sar-mrs-l2b"
EOS04_COLLECTION_ID = "EOS-04_SAR-MRS_L2B"
EOS04_PGSTAC_COLLECTION_ID = "akasha-eos-04-sar-mrs-l2b-backscatter-v1"
EOS04_PROCESSING_PROFILE_VERSION = "eos04-sar-mrs-l2b-gamma0-v2"
EOS04_NODATA = -9999.0
EOS04_DEFAULT_RESCALE = "-25,5"
EOS04_VALID_MASK_VALUE = 128
EOS04_POLARIZATIONS = ("HH", "HV", "VH", "VV", "RH", "RV")
_POLARIZATION_ORDER = {value: index for index, value in enumerate(EOS04_POLARIZATIONS)}
_POLARIZATION_PATTERN = re.compile(
    rf"(?:^|[^A-Z0-9])({'|'.join(EOS04_POLARIZATIONS)})(?:[^A-Z0-9]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SelectedEos04Product:
    product_id: str
    package_path: Path
    acquisition_at: datetime | None
    aoi_id: str
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedEos04Scene:
    product_id: str
    acquisition_at: datetime | None
    backscatter_path: Path
    checksum_sha256: str
    polarizations: tuple[str, ...]
    bbox: list[float]
    geometry: dict[str, Any]
    crs: str
    resolution: float
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Eos04L2bAssets:
    band_paths: tuple[Path, ...]
    mask_path: Path
    polarizations: tuple[str, ...]
    metadata: dict[str, str]
    calibration_constants_db: dict[str, float]
    noise_bias: dict[str, float]


class Eos04PrepareError(RuntimeError):
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


def normalize_polarizations(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = re.split(r"[,;\s]+", value.strip())
    elif isinstance(value, list | tuple):
        values = value
    else:
        values = ()
    normalized = {
        str(item).strip().upper()
        for item in values
        if str(item).strip().upper() in EOS04_POLARIZATIONS
    }
    return tuple(sorted(normalized, key=_POLARIZATION_ORDER.__getitem__))


def polarization_from_filename(path: str | Path) -> str | None:
    match = _POLARIZATION_PATTERN.search(Path(path).stem.upper())
    return match.group(1).upper() if match else None


def gamma0_dn_to_db(
    values: np.ndarray,
    *,
    calibration_constant_db: float,
    noise_bias: float,
) -> np.ndarray:
    """Calibrate EOS-04 L2B Gamma0 DN using NRSC format document equation 9.

    L2B stores uint16 amplitude-like DN. Gamma0 power is ``DN² - IMAGE_NOISE_BIAS``;
    its dB representation is ``10*log10(power) - Calibration_Constant_Beta0``.
    Non-positive corrected power is invalid.
    """
    source = values.astype("float64", copy=False)
    power = np.square(source) - noise_bias
    result = np.full(source.shape, np.nan, dtype="float64")
    valid = np.isfinite(power) & (power > 0)
    result[valid] = 10.0 * np.log10(power[valid]) - calibration_constant_db
    return result


def parse_band_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig", errors="strict").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in metadata:
            raise Eos04PrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                f"EOS-04 BAND_META contains duplicate key: {normalized_key}.",
            )
        metadata[normalized_key] = value.split("//", 1)[0].strip()
    return metadata


def prepare_eos04_product(
    product: SelectedEos04Product,
    settings: Settings,
    *,
    dry_run: bool = False,
    max_members: int = 2_000,
    max_expanded_bytes: int = 20 * 1024 * 1024 * 1024,
) -> PreparedEos04Scene:
    validate_resourcesat_runtime_roots(settings, dry_run=dry_run)
    work_root = Path(settings.scratch_dir) / "eos04-prepare" / _safe_component(product.product_id)
    extract_root = work_root / "extract"
    output_root = work_root / "prepared"
    if work_root.exists():
        shutil.rmtree(work_root)
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        safe_extract_product(
            product.package_path,
            extract_root,
            max_members=max_members,
            max_expanded_bytes=max_expanded_bytes,
        )
        assets = discover_l2b_assets(extract_root)
        output_path = output_root / "backscatter.tif"
        raster_metadata = write_calibrated_backscatter(assets, output_path)
        validate_eos04_cog(output_path, assets.polarizations)
        bbox = _wgs84_bounds(output_path)
        geometry = product.geometry or _bbox_geometry(bbox)
        checksum = file_sha256(output_path)
        calibration_formula = "10*log10(DN^2-IMAGE_NOISE_BIAS)-KCAL_BETA0_DB"
        comparison_metadata = normalize_eos04_comparison_metadata(
            assets.metadata,
            polarizations=assets.polarizations,
            processing_profile_version=EOS04_PROCESSING_PROFILE_VERSION,
            calibration_formula=calibration_formula,
            output_scale="db",
            resolution_meters=float(raster_metadata["resolution"]),
        )
        manifest = {
            "schema_version": "eos04-sar-prepare-v2",
            "source_id": EOS04_SOURCE_ID,
            "provider": "bhoonidhi",
            "provider_collection": EOS04_COLLECTION_ID,
            "product_id": product.product_id,
            "aoi_id": product.aoi_id,
            "acquisition_datetime": (
                product.acquisition_at.isoformat().replace("+00:00", "Z")
                if product.acquisition_at
                else None
            ),
            "processing_profile_version": EOS04_PROCESSING_PROFILE_VERSION,
            "input_representation": "uint16_gamma0_dn",
            "calibration_formula": calibration_formula,
            "output_scale": "db",
            "valid_mask_value": EOS04_VALID_MASK_VALUE,
            "rtc_apply_flag": int(assets.metadata["RTC_Apply_Flag"]),
            "missing_frames_flag": int(assets.metadata["Missing_Frames_Flag"]),
            "sar:frequency_band": "C",
            "sar:instrument_mode": "MRS",
            "sar:polarizations": list(assets.polarizations),
            "calibration_constants_db": assets.calibration_constants_db,
            "noise_bias": assets.noise_bias,
            "comparison_metadata": comparison_metadata,
            "bbox": bbox,
            "geometry": geometry,
            "crs": raster_metadata["crs"],
            "resolution": raster_metadata["resolution"],
            "outputs": {
                "backscatter": {
                    "path": str(output_path),
                    "checksum_sha256": checksum,
                    "dtype": "float32",
                    "nodata": EOS04_NODATA,
                    "unit": "dB",
                    "band_descriptions": list(assets.polarizations),
                    "valid_pixel_counts": raster_metadata["valid_pixel_counts"],
                }
            },
        }
        return PreparedEos04Scene(
            product_id=product.product_id,
            acquisition_at=product.acquisition_at,
            backscatter_path=output_path,
            checksum_sha256=checksum,
            polarizations=assets.polarizations,
            bbox=bbox,
            geometry=geometry,
            crs=str(raster_metadata["crs"]),
            resolution=float(raster_metadata["resolution"]),
            manifest=manifest,
        )
    except Eos04PrepareError:
        raise
    except Exception as exc:
        raise Eos04PrepareError(
            ProviderErrorCategory.PREPARE_FAILED,
            f"failed to prepare EOS-04 product: {product.product_id}",
            metadata={"product_id": product.product_id},
        ) from exc


def discover_l2b_assets(extract_root: Path) -> Eos04L2bAssets:
    files = [path for path in extract_root.rglob("*") if path.is_file()]
    band_meta_paths = [path for path in files if path.name.upper() == "BAND_META.TXT"]
    if len(band_meta_paths) != 1:
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 L2B archive must contain exactly one BAND_META.txt file.",
        )
    metadata = parse_band_metadata(band_meta_paths[0])
    _validate_l2b_metadata(metadata)
    polarizations = _metadata_polarizations(metadata)

    tiffs = [path for path in files if path.suffix.lower() in {".tif", ".tiff"}]
    mask_paths = [path for path in tiffs if path.stem.lower().endswith("_mask")]
    if len(mask_paths) != 1:
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 L2B archive must contain exactly one data mask GeoTIFF.",
        )
    named: dict[str, Path] = {}
    for path in tiffs:
        polarization = polarization_from_filename(path)
        if polarization is None:
            continue
        if polarization in named:
            raise Eos04PrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                f"EOS-04 archive contains ambiguous {polarization} backscatter assets.",
            )
        named[polarization] = path
    missing = [value for value in polarizations if value not in named]
    unexpected = sorted(set(named) - set(polarizations), key=_POLARIZATION_ORDER.__getitem__)
    if missing or unexpected:
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 polarization GeoTIFFs do not match BAND_META declarations.",
            metadata={"missing": missing, "unexpected": unexpected},
        )

    calibration_constants = {
        polarization: _required_float(metadata, f"Calibration_Constant_Beta0_{polarization}")
        for polarization in polarizations
    }
    noise_bias = {
        polarization: _required_float(metadata, f"Image_Noise_Bias_{polarization}", minimum=0)
        for polarization in polarizations
    }
    return Eos04L2bAssets(
        band_paths=tuple(named[value] for value in polarizations),
        mask_path=mask_paths[0],
        polarizations=polarizations,
        metadata=metadata,
        calibration_constants_db=calibration_constants,
        noise_bias=noise_bias,
    )


def write_calibrated_backscatter(
    assets: Eos04L2bAssets,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    intermediate = output_path.with_name("backscatter.source.tif")
    valid_counts = {polarization: 0 for polarization in assets.polarizations}
    try:
        with ExitStack() as stack:
            sources = [stack.enter_context(rasterio.open(path)) for path in assets.band_paths]
            mask = stack.enter_context(rasterio.open(assets.mask_path))
            reference = sources[0]
            _validate_l2b_grids(sources, mask)
            profile = {
                "driver": "GTiff",
                "width": reference.width,
                "height": reference.height,
                "count": len(sources),
                "dtype": "float32",
                "crs": reference.crs,
                "transform": reference.transform,
                "nodata": EOS04_NODATA,
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512,
                "compress": "deflate",
                "predictor": 3,
                "BIGTIFF": "IF_SAFER",
            }
            with rasterio.open(intermediate, "w", **profile) as target:
                target.update_tags(
                    AKASHA_SOURCE_ID=EOS04_SOURCE_ID,
                    AKASHA_COLLECTION_ID=EOS04_COLLECTION_ID,
                    AKASHA_PROCESSING_PROFILE_VERSION=EOS04_PROCESSING_PROFILE_VERSION,
                    AKASHA_INPUT_REPRESENTATION="uint16_gamma0_dn",
                    AKASHA_OUTPUT_SCALE="db",
                    AKASHA_VALID_MASK_VALUE=str(EOS04_VALID_MASK_VALUE),
                    AREA_OR_POINT="Area",
                )
                for band_index, polarization in enumerate(assets.polarizations, start=1):
                    target.set_band_description(band_index, polarization)
                    target.update_tags(
                        band_index,
                        UNIT="dB",
                        POLARIZATION=polarization,
                        CALIBRATION_CONSTANT_BETA0_DB=str(
                            assets.calibration_constants_db[polarization]
                        ),
                        IMAGE_NOISE_BIAS=str(assets.noise_bias[polarization]),
                    )
                for _, window in reference.block_windows(1):
                    valid_mask = mask.read(1, window=window) == EOS04_VALID_MASK_VALUE
                    for band_index, (polarization, source) in enumerate(
                        zip(assets.polarizations, sources, strict=True),
                        start=1,
                    ):
                        values = source.read(1, window=window)
                        calibrated = gamma0_dn_to_db(
                            values,
                            calibration_constant_db=assets.calibration_constants_db[polarization],
                            noise_bias=assets.noise_bias[polarization],
                        )
                        valid = valid_mask & np.isfinite(calibrated)
                        output = np.full(calibrated.shape, EOS04_NODATA, dtype="float32")
                        output[valid] = calibrated[valid].astype("float32", copy=False)
                        target.write(output, band_index, window=window)
                        valid_counts[polarization] += int(np.count_nonzero(valid))
            if any(count == 0 for count in valid_counts.values()):
                raise Eos04PrepareError(
                    ProviderErrorCategory.INVALID_PRODUCT,
                    "EOS-04 L2B scene contains no valid calibrated backscatter pixels.",
                    metadata={"valid_pixel_counts": valid_counts},
                )
            crs = reference.crs.to_string()
            resolution = max(abs(float(reference.transform.a)), abs(float(reference.transform.e)))
        translate_cog_file(intermediate, output_path, overview_resampling="average")
    finally:
        intermediate.unlink(missing_ok=True)
    return {"crs": crs, "resolution": resolution, "valid_pixel_counts": valid_counts}


def validate_eos04_cog(path: Path, polarizations: tuple[str, ...]) -> None:
    valid, errors, warnings = validate_cog(path)
    if not valid:
        raise Eos04PrepareError(
            ProviderErrorCategory.VALIDATION_FAILED,
            f"EOS-04 backscatter COG validation failed: {errors or warnings}",
        )
    with rasterio.open(path) as dataset:
        descriptions = tuple(value or "" for value in dataset.descriptions)
        if dataset.dtypes != tuple("float32" for _ in polarizations):
            raise Eos04PrepareError(
                ProviderErrorCategory.VALIDATION_FAILED,
                "EOS-04 backscatter COG must use Float32 bands.",
            )
        if descriptions != polarizations:
            raise Eos04PrepareError(
                ProviderErrorCategory.VALIDATION_FAILED,
                "EOS-04 backscatter COG band descriptions do not match polarizations.",
            )
        if dataset.nodata != EOS04_NODATA:
            raise Eos04PrepareError(
                ProviderErrorCategory.VALIDATION_FAILED,
                "EOS-04 backscatter COG has an invalid nodata value.",
            )


def _validate_l2b_metadata(metadata: dict[str, str]) -> None:
    required = {
        "ProductType": "L2B-ARD-PRODUCT",
        "SatID": "EOS-04",
        "Sensor": "SAR",
        "ImagingMode": "MRS",
        "RTC_Apply_Flag": "1",
        "Missing_Frames_Flag": "0",
    }
    mismatches = {
        key: {"expected": expected, "actual": metadata.get(key)}
        for key, expected in required.items()
        if metadata.get(key, "").strip().upper() != expected
    }
    if mismatches:
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 product does not satisfy the validated MRS L2B ARD profile.",
            metadata={"mismatches": mismatches},
        )


def _metadata_polarizations(metadata: dict[str, str]) -> tuple[str, ...]:
    try:
        count = int(metadata["NoOfPolarizations"])
    except (KeyError, ValueError) as exc:
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 BAND_META has an invalid NoOfPolarizations value.",
        ) from exc
    values = tuple(
        metadata.get(f"TxRxPol{index}", "").strip().upper()
        for index in range(1, count + 1)
    )
    if count < 1 or count > 4 or any(value not in EOS04_POLARIZATIONS for value in values):
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 BAND_META contains invalid polarization declarations.",
        )
    if len(set(values)) != len(values):
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 BAND_META contains duplicate polarization declarations.",
        )
    return values


def _required_float(
    metadata: dict[str, str],
    key: str,
    *,
    minimum: float | None = None,
) -> float:
    try:
        value = float(metadata[key])
    except (KeyError, ValueError) as exc:
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            f"EOS-04 BAND_META is missing a valid {key} value.",
        ) from exc
    if not np.isfinite(value) or value <= -9999 or (minimum is not None and value < minimum):
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            f"EOS-04 BAND_META contains an invalid {key} value.",
        )
    return value


def _validate_l2b_grids(sources: list[Any], mask: Any) -> None:
    reference = sources[0]
    expected = (reference.width, reference.height, reference.crs, reference.transform)
    if reference.count != 1 or reference.dtypes != ("uint16",):
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 L2B polarization rasters must be single-band uint16 GeoTIFFs.",
        )
    for source in [*sources[1:], mask]:
        current = (source.width, source.height, source.crs, source.transform)
        if current != expected or source.count != 1:
            raise Eos04PrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "EOS-04 L2B polarization and mask rasters must share one single-band grid.",
            )
    if mask.dtypes != ("uint16",):
        raise Eos04PrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "EOS-04 L2B data mask must be uint16.",
        )


def _wgs84_bounds(path: Path) -> list[float]:
    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise Eos04PrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "EOS-04 backscatter COG has no CRS.",
            )
        bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21)
    return [float(value) for value in bounds]


def _bbox_geometry(bbox: list[float]) -> dict[str, Any]:
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def _safe_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    return safe[:120] or "eos04"
