from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from json import dumps, loads
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text


@dataclass(slots=True)
class FieldQueryRecord:
    query_id: str
    field_geometry: dict[str, Any]
    index_name: str
    requested_date: date
    selection_reason: str
    crs: str = "EPSG:4326"
    selected_scene_id: str | None = None
    raster_output_id: str | None = None
    layer_id: str | None = None
    valid_pixel_count: int = 0
    stats_json: dict[str, Any] = field(default_factory=dict)
    class_area_json: list[dict[str, Any]] = field(default_factory=list)
    quality_json: dict[str, Any] = field(default_factory=dict)
    visualization_profile_id: str | None = None
    threshold_profile_id: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None


class InMemoryFieldQueryRepository:
    def __init__(self) -> None:
        self._queries: dict[str, FieldQueryRecord] = {}

    def save(self, record: FieldQueryRecord) -> FieldQueryRecord:
        record.created_at = record.created_at or datetime.now(UTC)
        self._queries[record.query_id] = record
        return record

    def get(self, query_id: str) -> FieldQueryRecord | None:
        return self._queries.get(query_id)


class DatabaseFieldQueryRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, record: FieldQueryRecord) -> FieldQueryRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.field_queries (
                        query_id,
                        field_geometry,
                        crs,
                        index_name,
                        requested_date,
                        selected_scene_id,
                        raster_output_id,
                        layer_id,
                        valid_pixel_count,
                        selection_reason,
                        stats_json,
                        class_area_json,
                        quality_json,
                        visualization_profile_id,
                        threshold_profile_id,
                        expires_at
                    )
                    VALUES (
                        :query_id,
                        ST_SetSRID(ST_GeomFromGeoJSON(:field_geometry), 4326),
                        :crs,
                        :index_name,
                        :requested_date,
                        CAST(:selected_scene_id AS uuid),
                        CAST(:raster_output_id AS uuid),
                        :layer_id,
                        :valid_pixel_count,
                        :selection_reason,
                        CAST(:stats_json AS jsonb),
                        CAST(:class_area_json AS jsonb),
                        CAST(:quality_json AS jsonb),
                        CAST(:visualization_profile_id AS uuid),
                        CAST(:threshold_profile_id AS uuid),
                        :expires_at
                    )
                    ON CONFLICT (query_id) DO UPDATE
                    SET stats_json = EXCLUDED.stats_json,
                        class_area_json = EXCLUDED.class_area_json,
                        quality_json = EXCLUDED.quality_json,
                        expires_at = EXCLUDED.expires_at
                    RETURNING *
                    """
                ),
                _params(record),
            ).mappings().one()
        record.created_at = row.created_at
        return record

    def get(self, query_id: str) -> FieldQueryRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT *, ST_AsGeoJSON(field_geometry) AS field_geometry_geojson "
                    "FROM akasha.field_queries WHERE query_id = :query_id"
                ),
                {"query_id": query_id},
            ).mappings().first()
        if row is None:
            return None
        geometry_geojson = row.field_geometry_geojson
        field_geometry = loads(geometry_geojson) if geometry_geojson else {}
        return FieldQueryRecord(
            query_id=row.query_id,
            field_geometry=field_geometry,
            index_name=row.index_name,
            requested_date=row.requested_date,
            selection_reason=row.selection_reason,
            crs=row.crs,
            selected_scene_id=str(row.selected_scene_id) if row.selected_scene_id else None,
            raster_output_id=str(row.raster_output_id) if row.raster_output_id else None,
            layer_id=row.layer_id,
            valid_pixel_count=row.valid_pixel_count,
            stats_json=dict(row.stats_json or {}),
            class_area_json=[dict(item) for item in row.class_area_json or []],
            quality_json=dict(row.quality_json or {}),
            visualization_profile_id=(
                str(row.visualization_profile_id) if row.visualization_profile_id else None
            ),
            threshold_profile_id=(
                str(row.threshold_profile_id) if row.threshold_profile_id else None
            ),
            expires_at=row.expires_at,
            created_at=row.created_at,
        )


def new_query_id() -> str:
    return f"fq_{uuid4().hex}"


def _params(record: FieldQueryRecord) -> dict[str, Any]:
    return {
        "query_id": record.query_id,
        "field_geometry": dumps(record.field_geometry),
        "crs": record.crs,
        "index_name": record.index_name,
        "requested_date": record.requested_date,
        "selected_scene_id": record.selected_scene_id,
        "raster_output_id": record.raster_output_id,
        "layer_id": record.layer_id,
        "valid_pixel_count": record.valid_pixel_count,
        "selection_reason": record.selection_reason,
        "stats_json": dumps(record.stats_json, sort_keys=True),
        "class_area_json": dumps(record.class_area_json, sort_keys=True),
        "quality_json": dumps(record.quality_json, sort_keys=True),
        "visualization_profile_id": record.visualization_profile_id,
        "threshold_profile_id": record.threshold_profile_id,
        "expires_at": record.expires_at,
    }
