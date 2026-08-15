from __future__ import annotations

import logging

import httpx

from akasha.config import Settings

logger = logging.getLogger(__name__)

# Per-index default TiTiler/rio-tiler colormap names. Kept intentionally small and
# deterministic; unknown indices render without a colormap (grayscale) rather than fail.
_INDEX_COLORMAPS: dict[str, str] = {
    "ndvi": "rdylgn",
    "ndmi": "rdylbu",
    "ndwi": "blues",
    "ndwi_green_nir": "blues",
    "msavi": "rdylgn",
}


class TiTilerError(Exception):
    """Raised when the internal TiTiler-PgSTAC backend fails.

    The message is always safe to surface to clients: it never embeds the internal
    TiTiler URL, query string, or upstream response body.
    """

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class TiTilerTileService:
    """Server-side proxy to the private TiTiler-PgSTAC service.

    The internal TiTiler URL is never exposed to callers; only PNG bytes and a
    content type are returned. All upstream errors are sanitized into
    :class:`TiTilerError` with a generic message.
    """

    def __init__(self, *, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.titiler_timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def colormap_for_index(self, index_name: str | None) -> str | None:
        if not index_name:
            return None
        return _INDEX_COLORMAPS.get(index_name.lower())

    def fetch_tile(
        self,
        *,
        collection_id: str,
        item_id: str,
        z: int,
        x: int,
        y: int,
        assets: str,
        asset_bidx: str | None = None,
        rescale: str | None = None,
        colormap_name: str | None = None,
    ) -> tuple[bytes, str]:
        path = (
            f"/collections/{collection_id}/items/{item_id}"
            f"/tiles/WebMercatorQuad/{z}/{x}/{y}.png"
        )
        url = f"{self._settings.titiler_internal_url.rstrip('/')}{path}"
        # TiTiler models ``assets`` as a repeated query parameter. A comma-delimited
        # value is treated as one literal asset name, which breaks RGB rendering.
        params: list[tuple[str, str]] = [
            ("assets", asset.strip()) for asset in assets.split(",") if asset.strip()
        ]
        if asset_bidx:
            params.append(("asset_bidx", asset_bidx))
        if rescale:
            params.append(("rescale", rescale))
        if colormap_name:
            params.append(("colormap_name", colormap_name))

        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning("titiler request failed for layer tile: %s", type(exc).__name__)
            raise TiTilerError("tile backend unavailable", status_code=502) from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            logger.info("titiler returned 404 for item %s", item_id)
            raise TiTilerError("tile not found", status_code=404)
        if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            logger.warning("titiler returned 422 for item %s", item_id)
            raise TiTilerError("tile request could not be processed", status_code=502)
        if response.status_code >= 400:
            logger.warning(
                "titiler returned %s for item %s", response.status_code, item_id
            )
            raise TiTilerError("tile backend error", status_code=502)

        return response.content, response.headers.get("content-type", "image/png")
