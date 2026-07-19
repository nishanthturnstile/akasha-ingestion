from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import rasterio
from affine import Affine
from rasterio.warp import transform_bounds

from akasha.config import Settings, validate_resourcesat_runtime_roots
from akasha.processing.cog import translate_cog_file, validate_cog
from akasha.processing.resourcesat_prepare import safe_extract_product
from akasha.providers.contracts import ProviderErrorCategory
from akasha.storage.object_store import file_sha256

NISAR_SOURCE_ID = "nisar-ssar-beta-gcov"
NISAR_COLLECTION_ID = "NISAR_SSAR-Beta_GCOV"
NISAR_PROVIDER_ROUTE = f"bhoonidhi:{NISAR_COLLECTION_ID}"
NISAR_PGSTAC_COLLECTION_ID = "akasha-nisar-ssar-beta-gcov-backscatter-v1"
NISAR_PROCESSING_PROFILE_VERSION = "nisar-ssar-beta-gcov-gamma0-v1"
NISAR_NODATA = -9999.0
NISAR_DEFAULT_RESCALE = "-25,5"
NISAR_POLARIZATIONS = ("HH", "HV", "VH", "VV")
NISAR_DIAGONAL_TERMS = {"HHHH": "HH", "HVHV": "HV", "VHVH": "VH", "VVVV": "VV"}

_IDENTIFICATION = "/science/SSAR/identification"
_GRID = "/science/SSAR/GCOV/grids/frequencyA"
_PROCESSING = "/science/SSAR/GCOV/metadata/processingInformation"


@dataclass(frozen=True, slots=True)
class SelectedNisarProduct:
    product_id: str
    package_path: Path
    acquisition_at: datetime | None
    aoi_id: str
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedNisarScene:
    product_id: str
    acquisition_at: datetime
    backscatter_path: Path
    checksum_sha256: str
    polarizations: tuple[str, ...]
    bbox: list[float]
    geometry: dict[str, Any]
    crs: str
    resolution: float
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NisarGcovAssets:
    hdf_path: Path
    terms: tuple[tuple[str, str], ...]
    mask_path: str
    number_of_subswaths: int
    crs: str
    transform: Affine
    width: int
    height: int
    x_spacing: float
    y_spacing: float
    metadata: dict[str, Any]


class NisarPrepareError(RuntimeError):
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


def gamma0_power_to_db(values: np.ndarray) -> np.ndarray:
    source = values.astype("float64", copy=False)
    result = np.full(source.shape, np.nan, dtype="float64")
    valid = np.isfinite(source) & (source > 0)
    result[valid] = 10.0 * np.log10(source[valid])
    return result


def prepare_nisar_product(
    product: SelectedNisarProduct,
    settings: Settings,
    *,
    dry_run: bool = False,
    max_members: int = 2_000,
    max_expanded_bytes: int = 40 * 1024 * 1024 * 1024,
) -> PreparedNisarScene:
    validate_resourcesat_runtime_roots(settings, dry_run=dry_run)
    work_root = Path(settings.scratch_dir) / "nisar-prepare" / _safe_component(product.product_id)
    extract_root = work_root / "extract"
    output_root = work_root / "prepared"
    if work_root.exists():
        shutil.rmtree(work_root)
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        if h5py.is_hdf5(product.package_path):
            extract_root.mkdir(parents=True, exist_ok=True)
            source_root = product.package_path.parent
            explicit_hdf = product.package_path
        else:
            source_root = safe_extract_product(
                product.package_path,
                extract_root,
                max_members=max_members,
                max_expanded_bytes=max_expanded_bytes,
            )
            explicit_hdf = None
        assets = discover_gcov_assets(source_root, explicit_hdf=explicit_hdf)
        intermediate = output_root / "backscatter.intermediate.tif"
        output_path = output_root / "backscatter.tif"
        valid_pixel_counts = write_gamma0_backscatter(assets, intermediate)
        translate_cog_file(
            intermediate,
            output_path,
            overview_resampling="average",
            bigtiff=True,
        )
        intermediate.unlink(missing_ok=True)
        validate_nisar_cog(output_path, tuple(pol for pol, _ in assets.terms))
        bbox = _wgs84_bounds(output_path)
        geometry = product.geometry or _bbox_geometry(bbox)
        checksum = file_sha256(output_path)
        acquisition_at = _parse_datetime(assets.metadata["zero_doppler_start_time"])
        if product.acquisition_at is not None:
            delta = abs((product.acquisition_at - acquisition_at).total_seconds())
            if delta > 1:
                raise NisarPrepareError(
                    ProviderErrorCategory.INVALID_PRODUCT,
                    "NISAR provider acquisition time conflicts with HDF5 identification metadata.",
                )
        polarizations = tuple(pol for pol, _ in assets.terms)
        manifest = {
            "schema_version": "nisar-ssar-gcov-prepare-v1",
            "source_id": NISAR_SOURCE_ID,
            "provider": "bhoonidhi",
            "provider_collection": NISAR_COLLECTION_ID,
            "product_id": product.product_id,
            "aoi_id": product.aoi_id,
            "acquisition_datetime": acquisition_at.isoformat().replace("+00:00", "Z"),
            "processing_profile_version": NISAR_PROCESSING_PROFILE_VERSION,
            "input_representation": "float32_gamma0_power",
            "calibration_formula": "10*log10(gamma0_power)",
            "output_scale": "db",
            "sar:frequency_band": "S",
            "sar:instrument_mode": "GCOV",
            "sar:polarizations": list(polarizations),
            "bbox": bbox,
            "geometry": geometry,
            "crs": assets.crs,
            "resolution": max(abs(assets.x_spacing), abs(assets.y_spacing)),
            "identification": assets.metadata,
            "outputs": {
                "backscatter": {
                    "path": str(output_path),
                    "checksum_sha256": checksum,
                    "dtype": "float32",
                    "nodata": NISAR_NODATA,
                    "unit": "dB",
                    "band_descriptions": list(polarizations),
                    "valid_pixel_counts": valid_pixel_counts,
                }
            },
        }
        return PreparedNisarScene(
            product_id=product.product_id,
            acquisition_at=acquisition_at,
            backscatter_path=output_path,
            checksum_sha256=checksum,
            polarizations=polarizations,
            bbox=bbox,
            geometry=geometry,
            crs=assets.crs,
            resolution=float(manifest["resolution"]),
            manifest=manifest,
        )
    except NisarPrepareError:
        raise
    except Exception as exc:
        raise NisarPrepareError(
            ProviderErrorCategory.PREPARE_FAILED,
            f"failed to prepare NISAR product: {product.product_id}",
            metadata={"product_id": product.product_id},
        ) from exc


def discover_gcov_assets(root: Path, *, explicit_hdf: Path | None = None) -> NisarGcovAssets:
    candidates = [explicit_hdf] if explicit_hdf else sorted(
        path
        for pattern in ("*.h5", "*.hdf5", "*.H5", "*.HDF5")
        for path in root.rglob(pattern)
        if path.is_file()
    )
    science_files: list[Path] = []
    for path in candidates:
        if path is None:
            continue
        try:
            with h5py.File(path, "r") as handle:
                if _IDENTIFICATION in handle and _GRID in handle:
                    science_files.append(path)
        except OSError:
            continue
    if len(science_files) != 1:
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "NISAR package must contain exactly one SSAR GCOV science HDF5 file.",
            metadata={"science_file_count": len(science_files)},
        )
    hdf_path = science_files[0]
    with h5py.File(hdf_path, "r") as handle:
        identification = handle[_IDENTIFICATION]
        grid = handle[_GRID]
        metadata = _identification_metadata(identification, handle)
        _validate_identification(metadata)
        _validate_science_filename(hdf_path, metadata)
        polarizations = _string_list(grid, "listOfPolarizations")
        covariance_terms = _string_list(grid, "listOfCovarianceTerms")
        if not polarizations or not covariance_terms:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "NISAR GCOV must declare polarizations and covariance terms.",
            )
        terms: list[tuple[str, str]] = []
        for term, polarization in NISAR_DIAGONAL_TERMS.items():
            if term in covariance_terms and polarization in polarizations and term in grid:
                dataset = grid[term]
                if not np.issubdtype(dataset.dtype, np.floating) or dataset.ndim != 2:
                    raise NisarPrepareError(
                        ProviderErrorCategory.INVALID_PRODUCT,
                        f"NISAR diagonal covariance term {term} must be a real 2D raster.",
                    )
                terms.append((polarization, f"{_GRID}/{term}"))
        if not terms:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "NISAR GCOV contains no declared real diagonal covariance terms.",
            )
        _validate_gamma0_contract(handle, metadata, terms)
        if "mask" not in grid or "numberOfSubSwaths" not in grid:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "NISAR GCOV is missing its native validity mask metadata.",
            )
        mask = grid["mask"]
        first = handle[terms[0][1]]
        if mask.shape != first.shape or mask.ndim != 2:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "NISAR GCOV mask and covariance grids do not match.",
            )
        for _, path in terms[1:]:
            if handle[path].shape != first.shape:
                raise NisarPrepareError(
                    ProviderErrorCategory.INVALID_PRODUCT,
                    "NISAR diagonal covariance terms do not share one grid.",
                )
        x_coordinates = np.asarray(grid["xCoordinates"][:], dtype="float64")
        y_coordinates = np.asarray(grid["yCoordinates"][:], dtype="float64")
        if len(x_coordinates) != first.shape[1] or len(y_coordinates) != first.shape[0]:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "NISAR coordinate arrays do not match the covariance grid.",
            )
        x_spacing = _validated_spacing(x_coordinates, "x")
        y_spacing = _validated_spacing(y_coordinates, "y")
        transform = Affine(
            x_spacing,
            0,
            x_coordinates[0] - x_spacing / 2,
            0,
            y_spacing,
            y_coordinates[0] - y_spacing / 2,
        )
        epsg = _epsg_code(grid["projection"])
        crs = f"EPSG:{epsg}"
        return NisarGcovAssets(
            hdf_path=hdf_path,
            terms=tuple(terms),
            mask_path=f"{_GRID}/mask",
            number_of_subswaths=int(_scalar(grid, "numberOfSubSwaths")),
            crs=crs,
            transform=transform,
            width=first.shape[1],
            height=first.shape[0],
            x_spacing=x_spacing,
            y_spacing=y_spacing,
            metadata=metadata,
        )


def write_gamma0_backscatter(assets: NisarGcovAssets, output_path: Path) -> list[int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    block_size = 512
    valid_counts = [0 for _ in assets.terms]
    profile = {
        "driver": "GTiff",
        "width": assets.width,
        "height": assets.height,
        "count": len(assets.terms),
        "dtype": "float32",
        "crs": assets.crs,
        "transform": assets.transform,
        "nodata": NISAR_NODATA,
        "tiled": True,
        "blockxsize": min(block_size, _valid_block_size(assets.width)),
        "blockysize": min(block_size, _valid_block_size(assets.height)),
        "compress": "DEFLATE",
        "predictor": 3,
        "BIGTIFF": "IF_SAFER",
    }
    with h5py.File(assets.hdf_path, "r") as handle, rasterio.open(
        output_path, "w", **profile
    ) as destination:
        mask_dataset = handle[assets.mask_path]
        datasets = [handle[path] for _, path in assets.terms]
        for row in range(0, assets.height, block_size):
            for col in range(0, assets.width, block_size):
                height = min(block_size, assets.height - row)
                width = min(block_size, assets.width - col)
                source_slice = np.s_[row : row + height, col : col + width]
                mask = np.asarray(mask_dataset[source_slice])
                native_valid = (mask >= 1) & (mask <= assets.number_of_subswaths)
                window = rasterio.windows.Window(col, row, width, height)
                for band_index, dataset in enumerate(datasets, start=1):
                    power = np.asarray(dataset[source_slice], dtype="float64")
                    valid = native_valid & np.isfinite(power) & (power > 0)
                    output = np.full(power.shape, NISAR_NODATA, dtype="float32")
                    output[valid] = gamma0_power_to_db(power[valid]).astype("float32")
                    valid_counts[band_index - 1] += int(valid.sum())
                    destination.write(output, band_index, window=window)
        for band_index, (polarization, _) in enumerate(assets.terms, start=1):
            destination.set_band_description(band_index, polarization)
            destination.update_tags(
                band_index,
                polarization=polarization,
                unit="dB",
                input_representation="gamma0_power",
            )
        destination.update_tags(
            AKASHA_SOURCE_ID=NISAR_SOURCE_ID,
            AKASHA_PROCESSING_PROFILE_VERSION=NISAR_PROCESSING_PROFILE_VERSION,
            AKASHA_BACKSCATTER_SCALE="dB",
            AKASHA_MASK_VALID_RULE="1..numberOfSubSwaths",
            AREA_OR_POINT="Area",
        )
    if not valid_counts or any(count <= 0 for count in valid_counts):
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "NISAR GCOV contains no valid Gamma0 pixels for one or more polarizations.",
        )
    return valid_counts


def validate_nisar_cog(path: Path, polarizations: tuple[str, ...]) -> None:
    valid, errors, warnings = validate_cog(path)
    if not valid:
        raise NisarPrepareError(
            ProviderErrorCategory.PREPARE_FAILED,
            f"NISAR backscatter COG validation failed: {errors or warnings}",
        )
    with rasterio.open(path) as dataset:
        if dataset.dtypes != tuple("float32" for _ in polarizations):
            raise NisarPrepareError(
                ProviderErrorCategory.PREPARE_FAILED,
                "NISAR backscatter COG must use Float32 bands.",
            )
        descriptions = tuple(value or "" for value in dataset.descriptions)
        if descriptions != polarizations or dataset.nodata != NISAR_NODATA:
            raise NisarPrepareError(
                ProviderErrorCategory.PREPARE_FAILED,
                "NISAR backscatter COG metadata does not match its polarization contract.",
            )
        if not dataset.crs or not dataset.overviews(1):
            raise NisarPrepareError(
                ProviderErrorCategory.PREPARE_FAILED,
                "NISAR backscatter COG requires a CRS and overviews.",
            )


def _identification_metadata(group: h5py.Group, handle: h5py.File) -> dict[str, Any]:
    processing_parameters = f"{_PROCESSING}/parameters"
    algorithms = f"{_PROCESSING}/algorithms"
    return {
        "mission_id": _optional_scalar(group, "missionId"),
        "instrument_name": _optional_scalar(group, "instrumentName")
        or _optional_scalar(group, "platformName"),
        "product_level": _optional_scalar(group, "productLevel"),
        "product_type": _optional_scalar(group, "productType"),
        "radar_band": _optional_scalar(group, "radarBand"),
        "granule_id": _optional_scalar(group, "granuleId"),
        "product_version": _optional_scalar(group, "productVersion"),
        "product_specification_version": _optional_scalar(
            group, "productSpecificationVersion"
        ),
        "zero_doppler_start_time": _optional_scalar(group, "zeroDopplerStartTime"),
        "zero_doppler_end_time": _optional_scalar(group, "zeroDopplerEndTime"),
        "orbit_pass_direction": _optional_scalar(group, "orbitPassDirection"),
        "look_direction": _optional_scalar(group, "lookDirection"),
        "track_number": _optional_scalar(group, "trackNumber"),
        "frame_number": _optional_scalar(group, "frameNumber"),
        "absolute_orbit_number": _optional_scalar(group, "absoluteOrbitNumber"),
        "radiometric_terrain_correction_applied": _optional_path_scalar(
            handle, f"{processing_parameters}/radiometricTerrainCorrectionApplied"
        ),
        "polarimetric_symmetrization_applied": _optional_path_scalar(
            handle, f"{processing_parameters}/polarimetricSymmetrizationApplied"
        ),
        "noise_correction_applied": _optional_path_scalar(
            handle, f"{processing_parameters}/noiseCorrectionApplied"
        ),
        "output_backscatter_normalization": _optional_path_scalar(
            handle, f"{processing_parameters}/rtc/outputBackscatterNormalizationConvention"
        ),
        "input_backscatter_normalization": _optional_path_scalar(
            handle, f"{processing_parameters}/rtc/inputBackscatterNormalizationConvention"
        ),
        "output_backscatter_expression": _optional_path_scalar(
            handle, f"{processing_parameters}/rtc/outputBackscatterExpressionConvention"
        ),
        "output_backscatter_decibel_formula": _optional_path_scalar(
            handle,
            "/science/SSAR/GCOV/metadata/ceosAnalysisReadyData/"
            "outputBackscatterDecibelConversionFormula",
        ),
        "software_version": _optional_path_scalar(handle, f"{algorithms}/softwareVersion"),
    }


def _validate_identification(metadata: dict[str, Any]) -> None:
    expected = {
        "mission_id": "NISAR",
        "instrument_name": "SSAR",
        "product_level": "L2",
        "product_type": "GCOV",
        "radar_band": "S",
    }
    for key, expected_value in expected.items():
        actual = str(metadata.get(key) or "").strip().upper().replace("-", "")
        expected_normalized = expected_value.upper().replace("-", "")
        if actual != expected_normalized:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                f"NISAR identification {key} must be {expected_value}.",
                metadata={"field": key, "actual": actual or None},
            )
    if not metadata.get("granule_id") or not metadata.get("zero_doppler_start_time"):
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "NISAR identification is missing granule ID or acquisition time.",
        )
    rtc = str(metadata.get("radiometric_terrain_correction_applied") or "").lower()
    if rtc not in {"true", "1"}:
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "NISAR GCOV requires radiometric terrain correction.",
        )


def _validate_gamma0_contract(
    handle: h5py.File,
    metadata: dict[str, Any],
    terms: list[tuple[str, str]],
) -> None:
    """Validate the Beta GCOV NRB/Gamma0 convention without guessing band order."""

    if not terms:
        return
    input_normalization = str(metadata.get("input_backscatter_normalization") or "").lower()
    output_normalization = str(metadata.get("output_backscatter_normalization") or "").lower()
    layer_descriptions = []
    for _, path in terms:
        dataset = handle[path]
        layer_descriptions.append(
            " ".join(
                str(_decode(dataset.attrs.get(key, "")))
                for key in ("description", "long_name")
            ).lower()
        )
    gamma0_declared = "gamma" in input_normalization or "gamma" in output_normalization
    gamma0_layers = all("gamma0" in value for value in layer_descriptions)
    formula = "".join(
        str(metadata.get("output_backscatter_decibel_formula") or "").lower().split()
    )
    if not gamma0_declared or not gamma0_layers or formula != "10*log10(<gcov_term>)":
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "NISAR GCOV diagonal covariance layers must declare linear Gamma0 and the "
            "10*log10 conversion formula.",
            metadata={
                "input_normalization": metadata.get("input_backscatter_normalization"),
                "output_normalization": metadata.get("output_backscatter_normalization"),
                "output_expression": metadata.get("output_backscatter_expression"),
                "decibel_formula": metadata.get("output_backscatter_decibel_formula"),
            },
        )


def _validate_science_filename(path: Path, metadata: dict[str, Any]) -> None:
    """Reject filenames that explicitly contradict qualified HDF5 metadata."""

    filename = path.stem.upper().replace("-", "_")
    conflicting_product_tokens = ("GSLC", "RSLC", "RIFG", "RUNW", "GUNW")
    conflict = next((token for token in conflicting_product_tokens if token in filename), None)
    if conflict or ("LSAR" in filename and "SSAR" not in filename):
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            "NISAR science filename conflicts with SSAR GCOV identification metadata.",
            metadata={
                "filename": path.name,
                "product_type": metadata.get("product_type"),
                "instrument_name": metadata.get("instrument_name"),
            },
        )


def _validated_spacing(coordinates: np.ndarray, axis: str) -> float:
    if coordinates.ndim != 1 or len(coordinates) < 2 or not np.isfinite(coordinates).all():
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            f"NISAR {axis}-coordinate array is invalid.",
        )
    differences = np.diff(coordinates)
    spacing = float(np.median(differences))
    if spacing == 0 or not np.allclose(differences, spacing, rtol=1e-6, atol=1e-6):
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            f"NISAR {axis}-coordinates are not uniformly spaced.",
        )
    return spacing


def _epsg_code(dataset: h5py.Dataset) -> int:
    values = [dataset[()], dataset.attrs.get("epsg_code")]
    for value in values:
        try:
            epsg = int(value)
        except (TypeError, ValueError):
            continue
        if epsg > 0:
            return epsg
    raise NisarPrepareError(
        ProviderErrorCategory.INVALID_PRODUCT,
        "NISAR GCOV projection does not declare a valid EPSG code.",
    )


def _scalar(group: h5py.Group, name: str) -> Any:
    if name not in group:
        raise NisarPrepareError(
            ProviderErrorCategory.INVALID_PRODUCT,
            f"NISAR HDF5 is missing required dataset {group.name}/{name}.",
        )
    return _decode(group[name][()])


def _optional_scalar(group: h5py.Group, name: str) -> Any:
    return _decode(group[name][()]) if name in group else None


def _optional_path_scalar(handle: h5py.File, path: str) -> Any:
    return _decode(handle[path][()]) if path in handle else None


def _string_list(group: h5py.Group, name: str) -> list[str]:
    value = _scalar(group, name)
    values = value.tolist() if isinstance(value, np.ndarray) else [value]
    return [str(_decode(item)).strip().upper() for item in values if str(_decode(item)).strip()]


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8").strip()
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8").strip()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return np.asarray([_decode(item) for item in value])
    return value


def _parse_datetime(value: Any) -> datetime:
    normalized = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _wgs84_bounds(path: Path) -> list[float]:
    with rasterio.open(path) as dataset:
        if not dataset.crs:
            raise NisarPrepareError(
                ProviderErrorCategory.INVALID_PRODUCT,
                "NISAR backscatter output has no CRS.",
            )
        return [
            float(value)
            for value in transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21)
        ]


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
    return safe[:120] or "nisar"


def _valid_block_size(size: int) -> int:
    if size >= 16:
        return max(16, (min(size, 512) // 16) * 16)
    return 16
