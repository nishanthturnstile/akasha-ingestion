from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform


@dataclass(frozen=True, slots=True)
class RasterBand:
    values: NDArray[np.number]
    transform: Any
    crs: str
    nodata: int | float | None


def read_single_band(payload: bytes) -> RasterBand:
    with MemoryFile(payload) as memory_file, memory_file.open() as dataset:
        return RasterBand(
            values=dataset.read(1),
            transform=dataset.transform,
            crs=str(dataset.crs),
            nodata=dataset.nodata,
        )


def raster_stats(
    payload: bytes,
    *,
    geometry: dict[str, Any],
    encoded_nodata: int | float | None,
    scale_factor: float | None,
    threshold_classes: list[dict[str, Any]],
) -> tuple[dict[str, float | int | None], list[dict[str, Any]]]:
    with MemoryFile(payload) as memory_file, memory_file.open() as dataset:
        values = dataset.read(1)
        dataset_geometry = _geometry_for_dataset(dataset, geometry)
        field_mask = geometry_mask(
            [dataset_geometry],
            out_shape=values.shape,
            transform=dataset.transform,
            invert=True,
        )
        valid = field_mask & np.isfinite(values)
        if encoded_nodata is not None:
            valid &= values != encoded_nodata
        selected = values[valid].astype("float32")
        if scale_factor is not None and scale_factor != 0:
            selected = selected / scale_factor
        stats = _stats(
            selected,
            valid_pixel_count=int(valid.sum()),
            field_pixel_count=int(field_mask.sum()),
        )
        class_stats = _class_stats(
            selected,
            threshold_classes=threshold_classes,
            pixel_area_sq_m=_pixel_area_sq_m(dataset, geometry),
        )
        return stats, class_stats


def _stats(
    selected: NDArray[np.floating],
    *,
    valid_pixel_count: int,
    field_pixel_count: int,
) -> dict[str, float | int | None]:
    if selected.size == 0:
        return {
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "stdDev": None,
            "usablePixelPercentage": 0.0,
            "validPixelCount": 0,
        }
    return {
        "min": float(np.min(selected)),
        "max": float(np.max(selected)),
        "mean": float(np.mean(selected)),
        "median": float(np.median(selected)),
        "stdDev": float(np.std(selected)),
        "usablePixelPercentage": (
            (valid_pixel_count / field_pixel_count) * 100 if field_pixel_count else 0.0
        ),
        "validPixelCount": valid_pixel_count,
    }


def _class_stats(
    selected: NDArray[np.floating],
    *,
    threshold_classes: list[dict[str, Any]],
    pixel_area_sq_m: float,
) -> list[dict[str, Any]]:
    if selected.size == 0:
        return []
    total_area = selected.size * pixel_area_sq_m
    results: list[dict[str, Any]] = []
    for item in threshold_classes:
        lower = float(item["min"])
        upper = float(item["max"])
        class_mask = (selected >= lower) & (selected < upper)
        count = int(class_mask.sum())
        area = count * pixel_area_sq_m
        results.append(
            {
                "class": item["label"],
                "valueRange": [lower, upper],
                "areaSqM": area,
                "areaPercentage": (area / total_area) * 100 if total_area else 0.0,
            }
        )
    return results


def _pixel_area_sq_m(dataset: rasterio.io.DatasetReader, geometry: dict[str, Any]) -> float:
    if dataset.crs and CRS.from_user_input(dataset.crs).is_projected:
        return abs(dataset.transform.a * dataset.transform.e)
    geom = shape(geometry)
    centroid = geom.centroid
    utm_zone = int((centroid.x + 180) // 6) + 1
    epsg = 32600 + utm_zone if centroid.y >= 0 else 32700 + utm_zone
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    projected = shapely_transform(transformer.transform, geom)
    return projected.area / max(_estimated_field_pixels(dataset, geometry), 1)


def _estimated_field_pixels(dataset: rasterio.io.DatasetReader, geometry: dict[str, Any]) -> int:
    dataset_geometry = _geometry_for_dataset(dataset, geometry)
    return int(
        geometry_mask(
            [dataset_geometry],
            out_shape=(dataset.height, dataset.width),
            transform=dataset.transform,
            invert=True,
        ).sum()
    )


def _geometry_for_dataset(
    dataset: rasterio.io.DatasetReader,
    geometry: dict[str, Any],
) -> dict[str, Any]:
    if dataset.crs is None or CRS.from_user_input(dataset.crs) == CRS.from_epsg(4326):
        return geometry
    transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
    projected = shapely_transform(transformer.transform, shape(geometry))
    return projected.__geo_interface__
