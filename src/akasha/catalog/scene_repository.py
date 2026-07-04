from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from json import dumps
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text


@dataclass(slots=True)
class ProviderSceneRecord:
    id: str | None
    provider_adapter: str
    source_id: str
    provider_product_id: str
    acquisition_at: datetime | None = None
    scene_geometry: dict[str, Any] | None = None
    status: str = "discovered"
    cloud_percent: float | None = None
    license_state: str = "unknown"
    pgstac_item_id: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    aoi_id: str | None = None
    provider_route_id: str | None = None
    logical_scene_key: str | None = None
    native_crs: str | None = None
    native_resolution: float | None = None
    coverage_percentage: float | None = None
    file_size_bytes: int | None = None
    raw_object_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InMemorySceneRepository:
    def __init__(self) -> None:
        self._scenes: dict[tuple[str, str], ProviderSceneRecord] = {}

    def upsert(self, scene: ProviderSceneRecord) -> ProviderSceneRecord:
        key = (scene.provider_adapter, scene.provider_product_id)
        now = datetime.now(UTC)
        existing = self._scenes.get(key)
        scene.id = existing.id if existing else scene.id or str(uuid4())
        scene.created_at = existing.created_at if existing else now
        scene.updated_at = now
        self._scenes[key] = scene
        return scene

    def get(self, scene_id: str) -> ProviderSceneRecord | None:
        return next((scene for scene in self._scenes.values() if scene.id == scene_id), None)

    def list_for_source_aoi(self, *, source_id: str, aoi_id: str) -> list[ProviderSceneRecord]:
        scenes = [
            scene
            for scene in self._scenes.values()
            if scene.source_id == source_id and scene.aoi_id == aoi_id
        ]
        scenes.sort(key=lambda scene: scene.acquisition_at or datetime.min.replace(tzinfo=UTC))
        return scenes

    def list_candidates(
        self,
        *,
        source_id: str,
        requested_date: date,
        window_days: int,
        max_cloud_percentage: float,
        limit: int,
    ) -> list[ProviderSceneRecord]:
        start = requested_date - timedelta(days=window_days)
        end = requested_date + timedelta(days=window_days)
        candidates = [
            scene
            for scene in self._scenes.values()
            if scene.source_id == source_id
            and scene.acquisition_at is not None
            and start <= scene.acquisition_at.date() <= end
            and (scene.cloud_percent is None or scene.cloud_percent <= max_cloud_percentage)
        ]
        candidates.sort(
            key=lambda scene: (
                abs((scene.acquisition_at.date() - requested_date).days)
                if scene.acquisition_at
                else 9999,
                scene.cloud_percent if scene.cloud_percent is not None else 101,
            )
        )
        return candidates[:limit]


class DatabaseSceneRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, scene: ProviderSceneRecord) -> ProviderSceneRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.provider_scenes (
                        provider_adapter,
                        source_id,
                        provider_product_id,
                        acquisition_at,
                        scene_geometry,
                        status,
                        cloud_percent,
                        license_state,
                        pgstac_item_id,
                        provider_metadata,
                        aoi_id,
                        provider_route_id,
                        logical_scene_key,
                        native_crs,
                        native_resolution,
                        coverage_percentage,
                        file_size_bytes,
                        raw_object_path
                    )
                    VALUES (
                        :provider_adapter,
                        :source_id,
                        :provider_product_id,
                        :acquisition_at,
                        CASE
                            WHEN CAST(:scene_geometry AS text) IS NULL THEN NULL
                            ELSE ST_SetSRID(
                                ST_GeomFromGeoJSON(CAST(:scene_geometry AS text)),
                                4326
                            )
                        END,
                        :status,
                        :cloud_percent,
                        :license_state,
                        :pgstac_item_id,
                        CAST(:provider_metadata AS jsonb),
                        :aoi_id,
                        CAST(:provider_route_id AS uuid),
                        :logical_scene_key,
                        :native_crs,
                        :native_resolution,
                        :coverage_percentage,
                        :file_size_bytes,
                        :raw_object_path
                    )
                    ON CONFLICT (provider_adapter, provider_product_id) DO UPDATE
                    SET source_id = EXCLUDED.source_id,
                        acquisition_at = EXCLUDED.acquisition_at,
                        scene_geometry = EXCLUDED.scene_geometry,
                        status = EXCLUDED.status,
                        cloud_percent = EXCLUDED.cloud_percent,
                        license_state = EXCLUDED.license_state,
                        pgstac_item_id = EXCLUDED.pgstac_item_id,
                        provider_metadata = EXCLUDED.provider_metadata,
                        aoi_id = EXCLUDED.aoi_id,
                        provider_route_id = EXCLUDED.provider_route_id,
                        logical_scene_key = EXCLUDED.logical_scene_key,
                        native_crs = EXCLUDED.native_crs,
                        native_resolution = EXCLUDED.native_resolution,
                        coverage_percentage = EXCLUDED.coverage_percentage,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        raw_object_path = EXCLUDED.raw_object_path,
                        updated_at = now()
                    RETURNING *, ST_AsGeoJSON(scene_geometry)::json AS scene_geometry_geojson
                    """
                ),
                _scene_params(scene),
            ).mappings().one()
        return _row_to_scene(row)

    def get(self, scene_id: str) -> ProviderSceneRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT *, ST_AsGeoJSON(scene_geometry)::json AS scene_geometry_geojson
                    FROM akasha.provider_scenes
                    WHERE id = CAST(:scene_id AS uuid)
                    """
                ),
                {"scene_id": scene_id},
            ).mappings().first()
        return _row_to_scene(row) if row else None

    def list_for_source_aoi(self, *, source_id: str, aoi_id: str) -> list[ProviderSceneRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT *, ST_AsGeoJSON(scene_geometry)::json AS scene_geometry_geojson
                    FROM akasha.provider_scenes
                    WHERE source_id = :source_id
                      AND aoi_id = :aoi_id
                    ORDER BY acquisition_at ASC NULLS LAST, created_at ASC
                    """
                ),
                {"source_id": source_id, "aoi_id": aoi_id},
            ).mappings().all()
        return [_row_to_scene(row) for row in rows]

    def list_candidates(
        self,
        *,
        source_id: str,
        requested_date: date,
        window_days: int,
        max_cloud_percentage: float,
        limit: int,
    ) -> list[ProviderSceneRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT *, ST_AsGeoJSON(scene_geometry)::json AS scene_geometry_geojson
                    FROM akasha.provider_scenes
                    WHERE source_id = :source_id
                      AND acquisition_at::date BETWEEN :start_date AND :end_date
                      AND (cloud_percent IS NULL OR cloud_percent <= :max_cloud_percentage)
                    ORDER BY
                      ABS(acquisition_at::date - :requested_date),
                      cloud_percent NULLS LAST,
                      acquisition_at DESC
                    LIMIT :limit
                    """
                ),
                {
                    "source_id": source_id,
                    "requested_date": requested_date,
                    "start_date": requested_date - timedelta(days=window_days),
                    "end_date": requested_date + timedelta(days=window_days),
                    "max_cloud_percentage": max_cloud_percentage,
                    "limit": limit,
                },
            ).mappings().all()
        return [_row_to_scene(row) for row in rows]


def _scene_params(scene: ProviderSceneRecord) -> dict[str, Any]:
    return {
        "provider_adapter": scene.provider_adapter,
        "source_id": scene.source_id,
        "provider_product_id": scene.provider_product_id,
        "acquisition_at": scene.acquisition_at,
        "scene_geometry": dumps(scene.scene_geometry) if scene.scene_geometry else None,
        "status": scene.status,
        "cloud_percent": scene.cloud_percent,
        "license_state": scene.license_state,
        "pgstac_item_id": scene.pgstac_item_id,
        "provider_metadata": dumps(scene.provider_metadata, sort_keys=True),
        "aoi_id": scene.aoi_id,
        "provider_route_id": scene.provider_route_id,
        "logical_scene_key": scene.logical_scene_key,
        "native_crs": scene.native_crs,
        "native_resolution": scene.native_resolution,
        "coverage_percentage": scene.coverage_percentage,
        "file_size_bytes": scene.file_size_bytes,
        "raw_object_path": scene.raw_object_path,
    }


def _row_to_scene(row: Any) -> ProviderSceneRecord:
    return ProviderSceneRecord(
        id=str(row.id),
        provider_adapter=row.provider_adapter,
        source_id=row.source_id,
        provider_product_id=row.provider_product_id,
        acquisition_at=row.acquisition_at,
        scene_geometry=(
            dict(row.get("scene_geometry_geojson"))
            if row.get("scene_geometry_geojson") is not None
            else None
        ),
        status=row.status,
        cloud_percent=float(row.cloud_percent) if row.cloud_percent is not None else None,
        license_state=row.license_state,
        pgstac_item_id=row.pgstac_item_id,
        provider_metadata=dict(row.provider_metadata or {}),
        aoi_id=row.aoi_id,
        provider_route_id=str(row.provider_route_id) if row.provider_route_id else None,
        logical_scene_key=row.logical_scene_key,
        native_crs=row.native_crs,
        native_resolution=(
            float(row.native_resolution) if row.native_resolution is not None else None
        ),
        coverage_percentage=(
            float(row.coverage_percentage) if row.coverage_percentage is not None else None
        ),
        file_size_bytes=row.file_size_bytes,
        raw_object_path=row.raw_object_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
