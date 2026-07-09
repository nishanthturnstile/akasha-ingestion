from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import Affine
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles


def write_cog_bytes(
    values: np.ndarray,
    *,
    transform: Affine,
    crs: str,
    nodata: int | float,
    tags: dict[str, str] | None = None,
) -> bytes:
    with TemporaryDirectory(prefix="akasha-cog-") as tmp_dir:
        source_path = Path(tmp_dir) / "source.tif"
        cog_path = Path(tmp_dir) / "output.cog.tif"
        with rasterio.open(
            source_path,
            "w",
            driver="GTiff",
            width=values.shape[1],
            height=values.shape[0],
            count=1,
            dtype=str(values.dtype),
            crs=crs,
            transform=transform,
            nodata=nodata,
        ) as dataset:
            dataset.write(values, 1)
            if tags:
                dataset.update_tags(**tags)

        profile = cog_profiles.get("deflate")
        cog_translate(
            source_path,
            cog_path,
            profile,
            in_memory=False,
            quiet=True,
        )
        is_valid, errors, warnings = cog_validate(cog_path, quiet=True)
        if not is_valid:
            raise ValueError(f"generated COG failed validation: {errors or warnings}")
        return cog_path.read_bytes()


def write_cog_file(
    values: np.ndarray,
    output_path: str | Path,
    *,
    transform: Affine,
    crs: str,
    nodata: int | float,
    tags: dict[str, str] | None = None,
    band_descriptions: tuple[str, ...] | None = None,
    overview_resampling: str = "bilinear",
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_values = values[np.newaxis, :, :] if values.ndim == 2 else values
    if source_values.ndim != 3:
        raise ValueError("COG values must be a 2D or 3D array")
    count, height, width = source_values.shape
    descriptions = band_descriptions or tuple(f"band-{index}" for index in range(1, count + 1))
    if len(descriptions) != count:
        raise ValueError("band description count must match COG band count")

    with TemporaryDirectory(prefix="akasha-cog-", dir=output_path.parent) as tmp_dir:
        source_path = Path(tmp_dir) / "source.tif"
        temp_output_path = Path(tmp_dir) / "output.cog.tif"
        with rasterio.open(
            source_path,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=count,
            dtype=str(source_values.dtype),
            crs=crs,
            transform=transform,
            nodata=nodata,
        ) as dataset:
            dataset.write(source_values)
            for band_index, description in enumerate(descriptions, start=1):
                dataset.set_band_description(band_index, description)
            if tags:
                dataset.update_tags(**tags)

        profile = cog_profiles.get("deflate")
        cog_translate(
            source_path,
            temp_output_path,
            profile,
            in_memory=False,
            overview_resampling=overview_resampling,
            quiet=True,
            forward_band_tags=True,
            forward_ns_tags=True,
        )
        is_valid, errors, warnings = cog_validate(temp_output_path, quiet=True)
        if not is_valid:
            raise ValueError(f"generated COG failed validation: {errors or warnings}")
        temp_output_path.replace(output_path)
    return output_path


def translate_cog_file(
    source_path: str | Path,
    output_path: str | Path,
    *,
    overview_resampling: str = "bilinear",
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="akasha-cog-", dir=output_path.parent) as tmp_dir:
        temp_output_path = Path(tmp_dir) / "output.cog.tif"
        profile = cog_profiles.get("deflate")
        cog_translate(
            source_path,
            temp_output_path,
            profile,
            in_memory=False,
            overview_resampling=overview_resampling,
            quiet=True,
            forward_band_tags=True,
            forward_ns_tags=True,
        )
        is_valid, errors, warnings = cog_validate(temp_output_path, quiet=True)
        if not is_valid:
            raise ValueError(f"generated COG failed validation: {errors or warnings}")
        temp_output_path.replace(output_path)
    return output_path


def validate_cog(path: str | Path) -> tuple[bool, list[str], list[str]]:
    is_valid, errors, warnings = cog_validate(path, quiet=True)
    return bool(is_valid), list(errors), list(warnings)


def cog_metadata(
    values: np.ndarray,
    *,
    crs: str,
    resolution: float,
    nodata: int | float | None = None,
) -> dict[str, Any]:
    valid_mask = np.isfinite(values)
    if nodata is not None:
        valid_mask &= values != nodata
    finite = values[valid_mask]
    return {
        "dtype": str(values.dtype),
        "crs": crs,
        "processing_resolution": resolution,
        "min_value": float(finite.min()) if finite.size else None,
        "max_value": float(finite.max()) if finite.size else None,
    }
