from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from rasterio.errors import WindowError
from rasterio.features import geometry_mask
from rasterio.windows import Window, intersection
from rasterio.windows import from_bounds as window_from_bounds
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

from akasha.processing.raster_source import RasterSource, open_raster


@dataclass(frozen=True, slots=True)
class RasterBand:
    values: NDArray[np.number]
    transform: Any
    crs: str
    nodata: int | float | None


def read_single_band(source: RasterSource) -> RasterBand:
    with open_raster(source) as dataset:
        return RasterBand(
            values=dataset.read(1),
            transform=dataset.transform,
            crs=str(dataset.crs),
            nodata=dataset.nodata,
        )


def raster_stats(
    source: RasterSource,
    *,
    geometry: dict[str, Any],
    encoded_nodata: int | float | None,
    scale_factor: float | None,
    threshold_classes: list[dict[str, Any]],
) -> tuple[dict[str, float | int | None], list[dict[str, Any]]]:
    with open_raster(source) as dataset:
        dataset_geometry = _geometry_for_dataset(dataset, geometry)
        # Read only the window covering the field polygon. Stats consider in-polygon
        # pixels only, so a windowed read is identical to a full read but far cheaper
        # for large scene COGs.
        window = _polygon_window(dataset, dataset_geometry)
        if window is None:
            empty = _stats(
                np.array([], dtype="float32"),
                valid_pixel_count=0,
                field_pixel_count=0,
            )
            return empty, []
        values = dataset.read(1, window=window)
        window_transform = dataset.window_transform(window)
        field_mask = geometry_mask(
            [dataset_geometry],
            out_shape=values.shape,
            transform=window_transform,
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
            pixel_area_sq_m=_pixel_area_sq_m(
                dataset,
                geometry,
                field_pixel_count=int(field_mask.sum()),
            ),
        )
        return stats, class_stats


def sar_field_stats(
    source: RasterSource,
    *,
    geometry: dict[str, Any],
    band_names: list[str],
    encoded_nodata: int | float | None,
) -> dict[str, Any]:
    """Return robust exact-field statistics for a calibrated multi-band SAR COG."""

    with open_raster(source) as dataset:
        dataset_geometry = _geometry_for_dataset(dataset, geometry)
        window = _polygon_window(dataset, dataset_geometry)
        if window is None:
            return {
                "fieldPixelCount": 0,
                "validPixelCount": 0,
                "coveragePercent": 0.0,
                "bands": [],
                "features": {},
            }
        values = dataset.read(window=window).astype("float64")
        field_mask = geometry_mask(
            [dataset_geometry],
            out_shape=values.shape[1:],
            transform=dataset.window_transform(window),
            invert=True,
        )
        field_pixels = int(field_mask.sum())
        if field_pixels <= 0:
            return {
                "fieldPixelCount": 0,
                "validPixelCount": 0,
                "coveragePercent": 0.0,
                "bands": [],
                "features": {},
            }

        resolved_nodata = dataset.nodata if dataset.nodata is not None else encoded_nodata
        # Evidence is qualified only where every advertised polarization is valid.
        # This prevents one healthy band from hiding gaps in another band used by a
        # cross-polarization feature such as HH-HV or VV-VH.
        common_valid = field_mask.copy()
        bands: list[dict[str, Any]] = []
        medians: dict[str, float] = {}
        for offset in range(values.shape[0]):
            band = values[offset]
            valid = field_mask & np.isfinite(band)
            if resolved_nodata is not None:
                valid &= band != float(resolved_nodata)
            common_valid &= valid
            selected = band[valid]
            polarization = (
                str(band_names[offset]).upper() if offset < len(band_names) else f"B{offset + 1}"
            )
            stats = _sar_band_stats(
                polarization,
                selected,
                field_pixel_count=field_pixels,
            )
            bands.append(stats)
            if stats["median"] is not None:
                medians[polarization] = float(stats["median"])

        valid_pixels = int(common_valid.sum())
        features: dict[str, float] = {}
        if "HH" in medians and "HV" in medians:
            features["HH_MINUS_HV_DB"] = medians["HH"] - medians["HV"]
        if "VV" in medians and "VH" in medians:
            features["VV_MINUS_VH_DB"] = medians["VV"] - medians["VH"]
        return {
            "fieldPixelCount": field_pixels,
            "validPixelCount": valid_pixels,
            "coveragePercent": _percentage(valid_pixels, field_pixels),
            "bands": bands,
            "features": features,
        }


def _sar_band_stats(
    polarization: str,
    selected: NDArray[np.floating],
    *,
    field_pixel_count: int,
) -> dict[str, Any]:
    if selected.size == 0:
        return {
            "polarization": polarization,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "stdDev": None,
            "p10": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "validPixelCount": 0,
            "validPixelPercent": 0.0,
            "unit": "dB",
        }
    percentiles = np.percentile(selected, [10, 25, 75, 90])
    return {
        "polarization": polarization,
        "min": float(np.min(selected)),
        "max": float(np.max(selected)),
        "mean": float(np.mean(selected)),
        "median": float(np.median(selected)),
        "stdDev": float(np.std(selected)),
        "p10": float(percentiles[0]),
        "p25": float(percentiles[1]),
        "p75": float(percentiles[2]),
        "p90": float(percentiles[3]),
        "validPixelCount": int(selected.size),
        "validPixelPercent": _percentage(selected.size, field_pixel_count),
        "unit": "dB",
    }


def categorical_mask_stats(
    source: RasterSource,
    *,
    geometry: dict[str, Any],
    nodata_classes: tuple[int, ...],
    usable_classes: tuple[int, ...],
    cloud_classes: tuple[int, ...],
    shadow_classes: tuple[int, ...],
) -> dict[str, float | int]:
    """Compute independent field coverage and quality percentages from a mask COG."""

    with open_raster(source) as dataset:
        dataset_geometry = _geometry_for_dataset(dataset, geometry)
        window = _polygon_window(dataset, dataset_geometry)
        if window is None:
            return _empty_mask_stats()
        values = dataset.read(1, window=window)
        field_mask = geometry_mask(
            [dataset_geometry],
            out_shape=values.shape,
            transform=dataset.window_transform(window),
            invert=True,
        )
        field_pixels = int(field_mask.sum())
        if field_pixels == 0:
            return _empty_mask_stats()

        covered = field_mask & ~np.isin(values, np.asarray(nodata_classes))
        usable = field_mask & np.isin(values, np.asarray(usable_classes))
        cloud = field_mask & np.isin(values, np.asarray(cloud_classes))
        shadow = field_mask & np.isin(values, np.asarray(shadow_classes))
        obscured = cloud | shadow
        return {
            "fieldPixelCount": field_pixels,
            "coveredPixelCount": int(covered.sum()),
            "usablePixelCount": int(usable.sum()),
            "cloudPixelCount": int(cloud.sum()),
            "shadowPixelCount": int(shadow.sum()),
            "fieldCoveragePercentage": _percentage(covered.sum(), field_pixels),
            "usablePixelPercentage": _percentage(usable.sum(), field_pixels),
            "cloudPercentage": _percentage(cloud.sum(), field_pixels),
            "shadowPercentage": _percentage(shadow.sum(), field_pixels),
            "obscuredPercentage": _percentage(obscured.sum(), field_pixels),
        }


def _empty_mask_stats() -> dict[str, float | int]:
    return {
        "fieldPixelCount": 0,
        "coveredPixelCount": 0,
        "usablePixelCount": 0,
        "cloudPixelCount": 0,
        "shadowPixelCount": 0,
        "fieldCoveragePercentage": 0.0,
        "usablePixelPercentage": 0.0,
        "cloudPercentage": 0.0,
        "shadowPercentage": 0.0,
        "obscuredPercentage": 0.0,
    }


def _percentage(numerator: Any, denominator: int) -> float:
    return (float(numerator) / denominator) * 100 if denominator else 0.0


def _polygon_window(dataset: rasterio.io.DatasetReader, dataset_geometry: dict[str, Any]):
    """Return the pixel window fully covering the polygon, clamped to the dataset.

    The window is expanded to whole pixels (floor near edge, ceil far edge) with a
    one-pixel pad so no polygon-boundary pixel is clipped: the in-polygon pixel set
    (and therefore the statistics) is identical to a full-band read. Returns ``None``
    when the polygon does not overlap the dataset.
    """

    minx, miny, maxx, maxy = shape(dataset_geometry).bounds
    window = window_from_bounds(minx, miny, maxx, maxy, transform=dataset.transform)
    col_off = math.floor(window.col_off) - 1
    row_off = math.floor(window.row_off) - 1
    width = math.ceil(window.col_off + window.width) - math.floor(window.col_off) + 2
    height = math.ceil(window.row_off + window.height) - math.floor(window.row_off) + 2
    padded = Window(col_off, row_off, width, height)
    ds_window = Window(0, 0, dataset.width, dataset.height)
    try:
        clamped = intersection(padded, ds_window)
    except WindowError:
        return None
    if clamped.width <= 0 or clamped.height <= 0:
        return None
    return clamped


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


def _pixel_area_sq_m(
    dataset: rasterio.io.DatasetReader,
    geometry: dict[str, Any],
    *,
    field_pixel_count: int,
) -> float:
    if dataset.crs and CRS.from_user_input(dataset.crs).is_projected:
        return abs(dataset.transform.a * dataset.transform.e)
    geom = shape(geometry)
    centroid = geom.centroid
    utm_zone = int((centroid.x + 180) // 6) + 1
    epsg = 32600 + utm_zone if centroid.y >= 0 else 32700 + utm_zone
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    projected = shapely_transform(transformer.transform, geom)
    return projected.area / max(field_pixel_count, 1)


def _geometry_for_dataset(
    dataset: rasterio.io.DatasetReader,
    geometry: dict[str, Any],
) -> dict[str, Any]:
    if dataset.crs is None or CRS.from_user_input(dataset.crs) == CRS.from_epsg(4326):
        return geometry
    transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
    projected = shapely_transform(transformer.transform, shape(geometry))
    return projected.__geo_interface__
