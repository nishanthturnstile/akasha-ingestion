from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from json import dumps, loads
from pathlib import Path
from threading import RLock
from typing import Any

from minio import Minio
from minio.error import S3Error

from akasha.config import Settings


class ObjectStoreNotFoundError(FileNotFoundError):
    pass


class ObjectStoreWriteError(RuntimeError):
    pass


class ObjectStoreReadError(RuntimeError):
    pass


class ObjectStat:
    def __init__(
        self,
        *,
        object_path: str,
        size_bytes: int,
        etag: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.object_path = object_path
        self.size_bytes = size_bytes
        self.etag = etag
        self.metadata = metadata or {}


class InMemoryObjectStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._objects: dict[str, bytes] = {}

    def put_raw_package(
        self,
        *,
        provider: str,
        source_id: str,
        product_id: str,
        payload: bytes,
    ) -> tuple[str, str]:
        checksum = sha256(payload).hexdigest()
        object_path = f"raw/{provider}/{source_id}/{product_id}/original.mock"
        with self._lock:
            self._objects[object_path] = payload
            self._objects[f"raw/{provider}/{source_id}/{product_id}/checksum.sha256"] = (
                checksum.encode("utf-8")
            )
        return object_path, checksum

    def put_raw_file(
        self,
        *,
        provider: str,
        source_id: str,
        product_id: str,
        file_path: Path,
        checksum_sha256: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"raw/{provider}/{source_id}/{product_id}/original.zip",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="application/zip",
            metadata=metadata,
        )

    def put_json(self, object_path: str, payload: dict[str, Any]) -> tuple[str, str]:
        return self.put_bytes(
            object_path,
            _json_bytes(payload),
            content_type="application/json",
        )

    def get_json(self, object_path: str) -> dict[str, Any]:
        payload = self.get_required(object_path)
        return loads(payload.decode("utf-8"))

    def put_bytes(
        self,
        object_path: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        del content_type, metadata
        checksum = sha256(payload).hexdigest()
        with self._lock:
            self._objects[object_path] = payload
            self._objects[f"{object_path}.sha256"] = checksum.encode("utf-8")
        return object_path, checksum

    def put_file(
        self,
        object_path: str,
        file_path: Path,
        *,
        checksum_sha256: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        del content_type, metadata
        payload = file_path.read_bytes()
        checksum = checksum_sha256 or sha256(payload).hexdigest()
        with self._lock:
            self._objects[object_path] = payload
            self._objects[f"{object_path}.sha256"] = checksum.encode("utf-8")
        return object_path, checksum

    def put_stac_item(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        item: dict[str, Any],
    ) -> tuple[str, str]:
        return self.put_json(
            f"raw/{provider}/{source_id}/{stac_item_id}/stac-item.json",
            item,
        )

    def put_asset_manifest(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        manifest: dict[str, Any],
    ) -> tuple[str, str]:
        return self.put_json(
            f"raw/{provider}/{source_id}/{stac_item_id}/asset-manifest.json",
            manifest,
        )

    def put_source_cog(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        asset_key: str,
        payload: bytes,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_bytes(
            f"raw/{provider}/{source_id}/{stac_item_id}/source-cogs/{asset_key}.tif",
            payload,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_source_cog_file(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        asset_key: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"raw/{provider}/{source_id}/{stac_item_id}/source-cogs/{asset_key}.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_prepared_cog_file(
        self,
        *,
        provider: str,
        source_id: str,
        product_id: str,
        asset_key: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"prepared/{provider}/{source_id}/{product_id}/{asset_key}.cog.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_composite_cog_file(
        self,
        *,
        source_id: str,
        aoi_id: str,
        composite_date: str,
        asset_key: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"composite/{source_id}/{aoi_id}/{composite_date}/{asset_key}.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_composite_manifest(
        self,
        *,
        source_id: str,
        aoi_id: str,
        composite_date: str,
        manifest: dict[str, Any],
    ) -> tuple[str, str]:
        return self.put_json(
            f"composite/{source_id}/{aoi_id}/{composite_date}/manifest.json",
            manifest,
        )

    def put_derived_cog(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        index_name: str,
        payload: bytes,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_bytes(
            f"indices/{provider}/{source_id}/{stac_item_id}/{index_name.lower()}.cog.tif",
            payload,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_derived_cog_file(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        index_name: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"indices/{provider}/{source_id}/{stac_item_id}/{index_name.lower()}.cog.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def get(self, object_path: str) -> bytes | None:
        with self._lock:
            return self._objects.get(object_path)

    def get_required(self, object_path: str) -> bytes:
        payload = self.get(object_path)
        if payload is None:
            raise ObjectStoreNotFoundError(object_path)
        return payload

    def exists(self, object_path: str) -> bool:
        with self._lock:
            return object_path in self._objects

    def stat(self, object_path: str) -> ObjectStat:
        with self._lock:
            payload = self._objects.get(object_path)
        if payload is None:
            raise ObjectStoreNotFoundError(object_path)
        return ObjectStat(object_path=object_path, size_bytes=len(payload))

    def health_check(self) -> bool:
        return True


class MinIOObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.minio_bucket
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put_raw_package(
        self,
        *,
        provider: str,
        source_id: str,
        product_id: str,
        payload: bytes,
    ) -> tuple[str, str]:
        self.ensure_bucket()
        checksum = sha256(payload).hexdigest()
        object_path = f"raw/{provider}/{source_id}/{product_id}/original.mock"
        checksum_path = f"raw/{provider}/{source_id}/{product_id}/checksum.sha256"
        self._client.put_object(
            self._bucket,
            object_path,
            BytesIO(payload),
            length=len(payload),
            content_type="application/octet-stream",
            metadata={"sha256": checksum, "source-id": source_id, "provider": provider},
        )
        checksum_bytes = checksum.encode()
        self._client.put_object(
            self._bucket,
            checksum_path,
            BytesIO(checksum_bytes),
            length=len(checksum_bytes),
            content_type="text/plain",
        )
        return object_path, checksum

    def put_raw_file(
        self,
        *,
        provider: str,
        source_id: str,
        product_id: str,
        file_path: Path,
        checksum_sha256: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"raw/{provider}/{source_id}/{product_id}/original.zip",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="application/zip",
            metadata=metadata,
        )

    def put_json(self, object_path: str, payload: dict[str, Any]) -> tuple[str, str]:
        return self.put_bytes(
            object_path,
            _json_bytes(payload),
            content_type="application/json",
        )

    def get_json(self, object_path: str) -> dict[str, Any]:
        payload = self.get_required(object_path)
        return loads(payload.decode("utf-8"))

    def put_bytes(
        self,
        object_path: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        self.ensure_bucket()
        checksum = sha256(payload).hexdigest()
        object_metadata = {"sha256": checksum}
        if metadata:
            object_metadata.update(metadata)
        try:
            self._client.put_object(
                self._bucket,
                object_path,
                BytesIO(payload),
                length=len(payload),
                content_type=content_type,
                metadata=object_metadata,
            )
            checksum_bytes = checksum.encode()
            self._client.put_object(
                self._bucket,
                f"{object_path}.sha256",
                BytesIO(checksum_bytes),
                length=len(checksum_bytes),
                content_type="text/plain",
            )
        except S3Error as exc:
            raise ObjectStoreWriteError(f"failed to write object: {object_path}") from exc
        return object_path, checksum

    def put_file(
        self,
        object_path: str,
        file_path: Path,
        *,
        checksum_sha256: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        self.ensure_bucket()
        checksum = checksum_sha256 or file_sha256(file_path)
        object_metadata = {"sha256": checksum}
        if metadata:
            object_metadata.update(metadata)
        try:
            self._client.fput_object(
                self._bucket,
                object_path,
                str(file_path),
                content_type=content_type,
                metadata=object_metadata,
            )
            checksum_bytes = checksum.encode()
            self._client.put_object(
                self._bucket,
                f"{object_path}.sha256",
                BytesIO(checksum_bytes),
                length=len(checksum_bytes),
                content_type="text/plain",
            )
        except S3Error as exc:
            raise ObjectStoreWriteError(f"failed to write object: {object_path}") from exc
        return object_path, checksum

    def put_stac_item(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        item: dict[str, Any],
    ) -> tuple[str, str]:
        return self.put_json(
            f"raw/{provider}/{source_id}/{stac_item_id}/stac-item.json",
            item,
        )

    def put_asset_manifest(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        manifest: dict[str, Any],
    ) -> tuple[str, str]:
        return self.put_json(
            f"raw/{provider}/{source_id}/{stac_item_id}/asset-manifest.json",
            manifest,
        )

    def put_source_cog(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        asset_key: str,
        payload: bytes,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_bytes(
            f"raw/{provider}/{source_id}/{stac_item_id}/source-cogs/{asset_key}.tif",
            payload,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_source_cog_file(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        asset_key: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"raw/{provider}/{source_id}/{stac_item_id}/source-cogs/{asset_key}.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_prepared_cog_file(
        self,
        *,
        provider: str,
        source_id: str,
        product_id: str,
        asset_key: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"prepared/{provider}/{source_id}/{product_id}/{asset_key}.cog.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_composite_cog_file(
        self,
        *,
        source_id: str,
        aoi_id: str,
        composite_date: str,
        asset_key: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"composite/{source_id}/{aoi_id}/{composite_date}/{asset_key}.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_composite_manifest(
        self,
        *,
        source_id: str,
        aoi_id: str,
        composite_date: str,
        manifest: dict[str, Any],
    ) -> tuple[str, str]:
        return self.put_json(
            f"composite/{source_id}/{aoi_id}/{composite_date}/manifest.json",
            manifest,
        )

    def put_derived_cog(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        index_name: str,
        payload: bytes,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_bytes(
            f"indices/{provider}/{source_id}/{stac_item_id}/{index_name.lower()}.cog.tif",
            payload,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def put_derived_cog_file(
        self,
        *,
        provider: str,
        source_id: str,
        stac_item_id: str,
        index_name: str,
        file_path: Path,
        checksum_sha256: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        return self.put_file(
            f"indices/{provider}/{source_id}/{stac_item_id}/{index_name.lower()}.cog.tif",
            file_path,
            checksum_sha256=checksum_sha256,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
            metadata=metadata,
        )

    def get(self, object_path: str) -> bytes | None:
        try:
            response = self._client.get_object(self._bucket, object_path)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception:
            return None

    def get_required(self, object_path: str) -> bytes:
        try:
            response = self._client.get_object(self._bucket, object_path)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise ObjectStoreNotFoundError(object_path) from exc
            raise ObjectStoreReadError(f"failed to read object: {object_path}") from exc

    def exists(self, object_path: str) -> bool:
        try:
            self._client.stat_object(self._bucket, object_path)
            return True
        except Exception:
            return False

    def stat(self, object_path: str) -> ObjectStat:
        try:
            result = self._client.stat_object(self._bucket, object_path)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise ObjectStoreNotFoundError(object_path) from exc
            raise ObjectStoreReadError(f"failed to stat object: {object_path}") from exc
        return ObjectStat(
            object_path=object_path,
            size_bytes=result.size,
            etag=result.etag,
            metadata=dict(result.metadata or {}),
        )

    def health_check(self) -> bool:
        self.ensure_bucket()
        return True


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
