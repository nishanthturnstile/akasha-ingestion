from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike

import rasterio
from rasterio.io import MemoryFile

RasterSource = bytes | bytearray | memoryview | str | PathLike[str]


@contextmanager
def open_raster(source: RasterSource) -> Iterator[rasterio.io.DatasetReader]:
    """Open byte-backed test rasters or range-readable runtime COG URLs."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        with MemoryFile(bytes(source)) as memory_file, memory_file.open() as dataset:
            yield dataset
        return

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
    ), rasterio.open(source) as dataset:
        yield dataset
