"""Field-clipped index overlay rendering.

Renders a single precomputed index COG (e.g. NDVI) as a colorized RGBA PNG that
is **clipped to the requested field polygon** and reprojected north-up to Web
Mercator, returning the image plus its EPSG:4326 corner coordinates. The output
is transparent everywhere outside the polygon and for nodata pixels, so the map
paints the index ONLY inside the drawn field (never the full scene tile).

Only a window covering the polygon is read from the COG, so this stays cheap
even for a full Sentinel-2 scene. The NDVI colour ramp mirrors the Akasha app
field-overlay legend so the map heatmap matches the UI legend classes.
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import Any

import numpy as np
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.features import rasterize
from rasterio.io import MemoryFile
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import reproject, transform, transform_bounds, transform_geom
from rasterio.windows import Window, intersection
from rasterio.windows import from_bounds as window_from_bounds
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

# NDVI reference classes matching the app field-overlay legend (apps/api/app/raster/tiles.py).
# (lower, upper, (r, g, b)).
_NDVI_REFERENCE_CLASSES: tuple[tuple[float, float, tuple[int, int, int]], ...] = (
    (-1.0, 0.0, (19, 24, 125)),
    (0.0, 0.15, (128, 70, 26)),
    (0.15, 0.30, (213, 0, 35)),
    (0.30, 0.45, (255, 83, 13)),
    (0.45, 0.60, (250, 201, 9)),
    (0.60, 0.75, (111, 202, 7)),
    (0.75, 0.90, (22, 153, 43)),
    (0.90, 1.0, (0, 88, 37)),
)

_DEFAULT_SUPERSAMPLE = 3
_DEFAULT_MAX_DIM = 2048


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", crc)


def _transparent_png() -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack("!IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


TRANSPARENT_PNG = _transparent_png()


def _rgba_png(width: int, height: int, rgba: bytes) -> bytes:
    if width <= 0 or height <= 0:
        return TRANSPARENT_PNG
    stride = width * 4
    if len(rgba) != stride * height:
        raise ValueError("RGBA buffer size does not match width*height*4.")
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0)
    scanlines = bytearray()
    for row in range(height):
        start = row * stride
        scanlines.append(0)
        scanlines.extend(rgba[start : start + stride])
    idat = zlib.compress(bytes(scanlines))
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _ndvi_palette(values: np.ndarray) -> np.ndarray:
    rgb = np.zeros(values.shape + (3,), dtype=np.uint8)
    for idx, (lower, upper, color) in enumerate(_NDVI_REFERENCE_CLASSES):
        if idx == len(_NDVI_REFERENCE_CLASSES) - 1:
            selected = (values >= lower) & (values <= upper)
        else:
            selected = (values >= lower) & (values < upper)
        rgb[selected] = np.array(color, dtype=np.uint8)
    rgb[values < _NDVI_REFERENCE_CLASSES[0][0]] = np.array(
        _NDVI_REFERENCE_CLASSES[0][2], dtype=np.uint8
    )
    rgb[values > _NDVI_REFERENCE_CLASSES[-1][1]] = np.array(
        _NDVI_REFERENCE_CLASSES[-1][2], dtype=np.uint8
    )
    return rgb


def _geometry_for_crs(geometry: dict[str, Any], dst_crs: Any) -> Any:
    geom = shape(geometry)
    if dst_crs is None or CRS.from_user_input(dst_crs) == CRS.from_epsg(4326):
        return geom
    transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    return shapely_transform(transformer.transform, geom)


def render_clipped_index_overlay(
    payload: bytes,
    *,
    geometry: dict[str, Any],
    index_name: str,
    scale_factor: float | None,
    nodata: int | float | None,
    supersample: int = _DEFAULT_SUPERSAMPLE,
    max_dim: int = _DEFAULT_MAX_DIM,
) -> tuple[bytes, list[list[float]] | None]:
    """Render a polygon-clipped, Web-Mercator index overlay PNG.

    Returns ``(png_bytes, corners)`` where ``corners`` are ``[TL, TR, BR, BL]``
    ``[lng, lat]`` pairs suitable for a MapLibre ``image`` source. When the
    polygon does not intersect valid raster data a fully transparent PNG is
    returned with ``corners=None``.
    """

    supersample = max(1, int(supersample))
    max_dim = max(1, int(max_dim))

    with MemoryFile(payload) as memory_file, memory_file.open() as dataset:
        src_crs = dataset.crs
        geom_ds = _geometry_for_crs(geometry, src_crs)
        minx, miny, maxx, maxy = geom_ds.bounds
        window = window_from_bounds(minx, miny, maxx, maxy, transform=dataset.transform)
        # Expand to whole pixels with a one-pixel pad so the polygon is never clipped
        # at the window edge (mirrors the statistics windowed read).
        col_off = math.floor(window.col_off) - 1
        row_off = math.floor(window.row_off) - 1
        width = math.ceil(window.col_off + window.width) - math.floor(window.col_off) + 2
        height = math.ceil(window.row_off + window.height) - math.floor(window.row_off) + 2
        padded = Window(col_off, row_off, width, height)
        ds_window = Window(0, 0, dataset.width, dataset.height)
        try:
            full = intersection(padded, ds_window)
        except WindowError:
            return TRANSPARENT_PNG, None
        if full.width <= 0 or full.height <= 0:
            return TRANSPARENT_PNG, None

        band = dataset.read(1, window=full).astype("float64")
        win_transform = dataset.window_transform(full)
        ds_nodata = dataset.nodata

    height, width = band.shape
    if height == 0 or width == 0:
        return TRANSPARENT_PNG, None

    valid = np.isfinite(band)
    if ds_nodata is not None:
        valid &= band != ds_nodata
    if nodata is not None:
        valid &= band != float(nodata)

    values = band.copy()
    if scale_factor is not None and scale_factor != 0:
        values = values / float(scale_factor)
    values[~valid] = np.nan

    # Reproject the source window (UTM) to a north-up Web Mercator grid, supersampled
    # for a smooth heatmap, then clip crisply to the polygon at the fine output grid.
    left, bottom, right, top = array_bounds(height, width, win_transform)
    dst_crs = "EPSG:3857"
    mleft, mbottom, mright, mtop = transform_bounds(
        src_crs, dst_crs, left, bottom, right, top, densify_pts=21
    )
    src_res = (abs(win_transform.a) + abs(win_transform.e)) / 2.0 or 1.0
    out_res = max(src_res / supersample, 1.0)
    out_w = max(1, int(round((mright - mleft) / out_res)))
    out_h = max(1, int(round((mtop - mbottom) / out_res)))
    scale = max(out_w / max_dim, out_h / max_dim, 1.0)
    if scale > 1.0:
        out_w = max(1, int(out_w / scale))
        out_h = max(1, int(out_h / scale))
    out_transform = from_bounds(mleft, mbottom, mright, mtop, out_w, out_h)

    common = {
        "src_transform": win_transform,
        "src_crs": src_crs,
        "dst_transform": out_transform,
        "dst_crs": dst_crs,
    }
    out_index = np.full((out_h, out_w), np.nan, dtype="float64")
    reproject(
        values,
        out_index,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
        **common,
    )
    out_valid = np.zeros((out_h, out_w), dtype="float32")
    reproject(valid.astype("float32"), out_valid, resampling=Resampling.bilinear, **common)

    geom_3857 = transform_geom("EPSG:4326", dst_crs, geometry)
    poly = rasterize(
        [(geom_3857, 1)],
        out_shape=(out_h, out_w),
        transform=out_transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)

    rgba = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    valid_b = poly & (out_valid >= 0.5) & np.isfinite(out_index)
    if np.any(valid_b):
        # Pipeline overlays are NDVI only; use the reference legend palette.
        rgb = _ndvi_palette(out_index)
        rgba[valid_b, :3] = rgb[valid_b]
        rgba[valid_b, 3] = 255

    if not np.any(rgba[..., 3]):
        return TRANSPARENT_PNG, None

    xs, ys = transform(
        dst_crs,
        "EPSG:4326",
        [mleft, mright, mright, mleft],
        [mtop, mtop, mbottom, mbottom],
    )
    corners = [[round(float(x), 10), round(float(y), 10)] for x, y in zip(xs, ys, strict=True)]
    return _rgba_png(out_w, out_h, rgba.tobytes()), corners
