from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from json import dumps
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text


@dataclass(slots=True)
class SceneAssetRecord:
    id: str | None
    scene_id: str
    asset_kind: str
    asset_key: str | None = None
    band_role: str | None = None
    object_path: str | None = None
    asset_href: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = None
    storage_backend: str = "minio"
    storage_region: str | None = None
    requester_pays: bool = False
    scale: float | None = None
    offset: float | None = None
    nodata_value: float | int | None = None
    roles: list[str] = field(default_factory=list)
    media_type: str | None = None
    mirror_status: str = "not_required"
    mirror_object_path: str | None = None
    mirror_checksum_sha256: str | None = None
    selected_access_mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


class InMemorySceneAssetRepository:
    def __init__(self) -> None:
        self._assets: dict[tuple[str, str], SceneAssetRecord] = {}

    def upsert(self, asset: SceneAssetRecord) -> SceneAssetRecord:
        if asset.asset_key is None:
            raise ValueError("asset_key is required for deterministic asset upserts")
        key = (asset.scene_id, asset.asset_key)
        existing = self._assets.get(key)
        asset.id = existing.id if existing else asset.id or str(uuid4())
        asset.created_at = existing.created_at if existing else datetime.now(UTC)
        if (
            existing
            and asset.mirror_object_path is None
            and existing.mirror_object_path is not None
        ):
            asset.mirror_status = existing.mirror_status
            asset.mirror_object_path = existing.mirror_object_path
            asset.mirror_checksum_sha256 = existing.mirror_checksum_sha256
            asset.size_bytes = (
                asset.size_bytes if asset.size_bytes is not None else existing.size_bytes
            )
        self._assets[key] = asset
        return asset

    def list_for_scene(self, scene_id: str) -> list[SceneAssetRecord]:
        return [asset for asset in self._assets.values() if asset.scene_id == scene_id]

    def update_mirror(
        self,
        asset_id: str,
        *,
        mirror_status: str,
        mirror_object_path: str | None,
        mirror_checksum_sha256: str | None,
        size_bytes: int | None = None,
    ) -> SceneAssetRecord:
        for key, asset in self._assets.items():
            if asset.id == asset_id:
                asset.mirror_status = mirror_status
                asset.mirror_object_path = mirror_object_path
                asset.mirror_checksum_sha256 = mirror_checksum_sha256
                asset.size_bytes = size_bytes if size_bytes is not None else asset.size_bytes
                self._assets[key] = asset
                return asset
        raise ValueError(f"asset not found: {asset_id}")


class DatabaseSceneAssetRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, asset: SceneAssetRecord) -> SceneAssetRecord:
        if asset.asset_key is None:
            raise ValueError("asset_key is required for deterministic asset upserts")
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.scene_assets (
                        scene_id,
                        asset_kind,
                        band_role,
                        object_path,
                        checksum_sha256,
                        size_bytes,
                        metadata,
                        asset_href,
                        storage_backend,
                        storage_region,
                        requester_pays,
                        asset_key,
                        scale,
                        offset_value,
                        nodata_value,
                        roles,
                        media_type,
                        mirror_status,
                        mirror_object_path,
                        mirror_checksum_sha256,
                        selected_access_mode
                    )
                    VALUES (
                        CAST(:scene_id AS uuid),
                        :asset_kind,
                        :band_role,
                        :object_path,
                        :checksum_sha256,
                        :size_bytes,
                        CAST(:metadata AS jsonb),
                        :asset_href,
                        :storage_backend,
                        :storage_region,
                        :requester_pays,
                        :asset_key,
                        :scale,
                        :offset_value,
                        :nodata_value,
                        :roles,
                        :media_type,
                        :mirror_status,
                        :mirror_object_path,
                        :mirror_checksum_sha256,
                        :selected_access_mode
                    )
                    ON CONFLICT (scene_id, asset_key) WHERE asset_key IS NOT NULL DO UPDATE
                    SET asset_kind = EXCLUDED.asset_kind,
                        band_role = EXCLUDED.band_role,
                        object_path = EXCLUDED.object_path,
                        checksum_sha256 = EXCLUDED.checksum_sha256,
                        size_bytes = COALESCE(
                            EXCLUDED.size_bytes,
                            akasha.scene_assets.size_bytes
                        ),
                        metadata = EXCLUDED.metadata,
                        asset_href = EXCLUDED.asset_href,
                        storage_backend = EXCLUDED.storage_backend,
                        storage_region = EXCLUDED.storage_region,
                        requester_pays = EXCLUDED.requester_pays,
                        scale = EXCLUDED.scale,
                        offset_value = EXCLUDED.offset_value,
                        nodata_value = EXCLUDED.nodata_value,
                        roles = EXCLUDED.roles,
                        media_type = EXCLUDED.media_type,
                        mirror_status = CASE
                            WHEN EXCLUDED.mirror_object_path IS NULL
                             AND akasha.scene_assets.mirror_object_path IS NOT NULL
                            THEN akasha.scene_assets.mirror_status
                            ELSE EXCLUDED.mirror_status
                        END,
                        mirror_object_path = COALESCE(
                            EXCLUDED.mirror_object_path,
                            akasha.scene_assets.mirror_object_path
                        ),
                        mirror_checksum_sha256 = COALESCE(
                            EXCLUDED.mirror_checksum_sha256,
                            akasha.scene_assets.mirror_checksum_sha256
                        ),
                        selected_access_mode = EXCLUDED.selected_access_mode
                    RETURNING *
                    """
                ),
                _asset_params(asset),
            ).mappings().one()
        return _row_to_asset(row)

    def list_for_scene(self, scene_id: str) -> list[SceneAssetRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.scene_assets
                    WHERE scene_id = CAST(:scene_id AS uuid)
                    ORDER BY asset_key, asset_kind
                    """
                ),
                {"scene_id": scene_id},
            ).mappings().all()
        return [_row_to_asset(row) for row in rows]

    def update_mirror(
        self,
        asset_id: str,
        *,
        mirror_status: str,
        mirror_object_path: str | None,
        mirror_checksum_sha256: str | None,
        size_bytes: int | None = None,
    ) -> SceneAssetRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE akasha.scene_assets
                    SET mirror_status = :mirror_status,
                        mirror_object_path = :mirror_object_path,
                        mirror_checksum_sha256 = :mirror_checksum_sha256,
                        size_bytes = COALESCE(:size_bytes, size_bytes)
                    WHERE id = CAST(:asset_id AS uuid)
                    RETURNING *
                    """
                ),
                {
                    "asset_id": asset_id,
                    "mirror_status": mirror_status,
                    "mirror_object_path": mirror_object_path,
                    "mirror_checksum_sha256": mirror_checksum_sha256,
                    "size_bytes": size_bytes,
                },
            ).mappings().one()
        return _row_to_asset(row)


def _asset_params(asset: SceneAssetRecord) -> dict[str, Any]:
    return {
        "scene_id": asset.scene_id,
        "asset_kind": asset.asset_kind,
        "band_role": asset.band_role,
        "object_path": asset.object_path,
        "checksum_sha256": asset.checksum_sha256,
        "size_bytes": asset.size_bytes,
        "metadata": dumps(asset.metadata, sort_keys=True),
        "asset_href": asset.asset_href,
        "storage_backend": asset.storage_backend,
        "storage_region": asset.storage_region,
        "requester_pays": asset.requester_pays,
        "asset_key": asset.asset_key,
        "scale": asset.scale,
        "offset_value": asset.offset,
        "nodata_value": asset.nodata_value,
        "roles": asset.roles,
        "media_type": asset.media_type,
        "mirror_status": asset.mirror_status,
        "mirror_object_path": asset.mirror_object_path,
        "mirror_checksum_sha256": asset.mirror_checksum_sha256,
        "selected_access_mode": asset.selected_access_mode,
    }


def _row_to_asset(row: Any) -> SceneAssetRecord:
    return SceneAssetRecord(
        id=str(row.id),
        scene_id=str(row.scene_id),
        asset_kind=row.asset_kind,
        asset_key=row.asset_key,
        band_role=row.band_role,
        object_path=row.object_path,
        asset_href=row.asset_href,
        checksum_sha256=row.checksum_sha256,
        size_bytes=row.size_bytes,
        storage_backend=row.storage_backend,
        storage_region=row.storage_region,
        requester_pays=row.requester_pays,
        scale=float(row.scale) if row.scale is not None else None,
        offset=float(row.offset_value) if row.offset_value is not None else None,
        nodata_value=row.nodata_value,
        roles=[str(role) for role in row.roles or []],
        media_type=row.media_type,
        mirror_status=row.mirror_status,
        mirror_object_path=row.mirror_object_path,
        mirror_checksum_sha256=row.mirror_checksum_sha256,
        selected_access_mode=row.selected_access_mode,
        metadata=dict(row.metadata or {}),
        created_at=row.created_at,
    )
