from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from threading import RLock

from minio import Minio

from akasha.config import Settings


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

    def get(self, object_path: str) -> bytes | None:
        with self._lock:
            return self._objects.get(object_path)

    def exists(self, object_path: str) -> bool:
        with self._lock:
            return object_path in self._objects

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

    def exists(self, object_path: str) -> bool:
        try:
            self._client.stat_object(self._bucket, object_path)
            return True
        except Exception:
            return False

    def health_check(self) -> bool:
        self.ensure_bucket()
        return True
