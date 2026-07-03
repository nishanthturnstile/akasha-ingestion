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
