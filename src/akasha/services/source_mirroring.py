from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import Any

import httpx

from akasha.config import Settings
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem


@dataclass(frozen=True, slots=True)
class MirrorResult:
    asset_key: str
    object_path: str
    checksum_sha256: str
    size_bytes: int
    metadata_path: str
    metadata_checksum_sha256: str


class SourceMirroringService:
    def __init__(
        self,
        *,
        object_store,
        settings: Settings,
        client: httpx.Client | None = None,
    ) -> None:
        self._object_store = object_store
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.earthsearch_timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def mirror_asset(
        self,
        *,
        item: NormalizedStacItem,
        asset: NormalizedAsset,
        payload: bytes | None = None,
        download_href: str | None = None,
    ) -> MirrorResult:
        if payload is not None:
            object_path, checksum = self._object_store.put_source_cog(
                provider=item.provider_adapter,
                source_id=item.source_id,
                stac_item_id=item.stac_item_id,
                asset_key=asset.asset_key,
                payload=payload,
                metadata={"source-id": item.source_id, "stac-item-id": item.stac_item_id},
            )
            size_bytes = len(payload)
        else:
            self._settings.scratch_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(
                prefix="akasha-source-mirror-",
                dir=str(self._settings.scratch_dir),
            ) as tmp_dir:
                file_path = Path(tmp_dir) / f"{asset.asset_key}.tif"
                checksum, size_bytes = self._download_asset(
                    asset,
                    file_path,
                    download_href=download_href,
                )
                object_path, checksum = self._object_store.put_source_cog_file(
                    provider=item.provider_adapter,
                    source_id=item.source_id,
                    stac_item_id=item.stac_item_id,
                    asset_key=asset.asset_key,
                    file_path=file_path,
                    checksum_sha256=checksum,
                    metadata={"source-id": item.source_id, "stac-item-id": item.stac_item_id},
                )
        metadata = _mirror_metadata(
            item=item,
            asset=asset,
            object_path=object_path,
            checksum=checksum,
            mirror_mode=self._settings.source_mirror_mode.value,
        )
        metadata_path, metadata_checksum = self._object_store.put_json(
            f"raw/{item.provider_adapter}/{item.source_id}/{item.stac_item_id}/"
            f"source-cogs/{asset.asset_key}.metadata.json",
            metadata,
        )
        return MirrorResult(
            asset_key=asset.asset_key,
            object_path=object_path,
            checksum_sha256=checksum,
            size_bytes=size_bytes,
            metadata_path=metadata_path,
            metadata_checksum_sha256=metadata_checksum,
        )

    def _download_asset(
        self,
        asset: NormalizedAsset,
        file_path: Path,
        *,
        download_href: str | None = None,
    ) -> tuple[str, int]:
        max_bytes = self._settings.source_mirror_max_bytes_per_run
        digest = sha256()
        size_bytes = 0
        for attempt in range(self._settings.provider_retry_attempts):
            digest = sha256()
            size_bytes = 0
            try:
                with self._client.stream("GET", download_href or asset.href) as response:
                    response.raise_for_status()
                    with file_path.open("wb") as file:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            size_bytes += len(chunk)
                            if max_bytes is not None and size_bytes > max_bytes:
                                raise ValueError("source mirror byte limit exceeded")
                            digest.update(chunk)
                            file.write(chunk)
                return digest.hexdigest(), size_bytes
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt + 1 >= self._settings.provider_retry_attempts:
                    raise
                sleep(self._settings.provider_retry_backoff_seconds * (2**attempt))
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 >= self._settings.provider_retry_attempts:
                    raise
                sleep(self._settings.provider_retry_backoff_seconds * (2**attempt))
        raise AssertionError("unreachable source mirror retry state")


def _mirror_metadata(
    *,
    item: NormalizedStacItem,
    asset: NormalizedAsset,
    object_path: str,
    checksum: str,
    mirror_mode: str,
) -> dict[str, Any]:
    return {
        "schema_version": "phase2-source-mirror-v1",
        "provider_adapter": item.provider_adapter,
        "source_id": item.source_id,
        "stac_item_id": item.stac_item_id,
        "asset_key": asset.asset_key,
        "source_href": asset.href,
        "alternate_hrefs": asset.alternate_hrefs,
        "mirror_object_path": object_path,
        "mirror_checksum_sha256": checksum,
        "mirror_mode": mirror_mode,
        "scale": asset.scale,
        "offset": asset.offset,
        "nodata": asset.nodata,
    }
