from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from json import dumps
from typing import Any

from sqlalchemy import Engine, text

DEFAULT_DERIVED_COLLECTION_ID = "akasha-sentinel-2-l2a-derived-v1"


@dataclass(slots=True)
class TileLayerRecord:
    layer_id: str | None
    raster_output_id: str
    visibility: str = "private"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class TileLayerResolution:
    """A tile layer joined to its raster output and pgSTAC item.

    Carries everything the tile bridge needs to render via TiTiler-PgSTAC:
    the object path (for logging/verification), index name, pgSTAC collection,
    asset key, scene/item id, and the value range for rescaling.
    """

    layer_id: str
    raster_output_id: str
    visibility: str
    object_path: str
    index_name: str | None
    scene_id: str
    collection_id: str
    asset_key: str
    item_id: str | None
    min_value: float | None
    max_value: float | None


class InMemoryTileLayerRepository:
    def __init__(self, *, raster_repository: Any = None, scene_repository: Any = None) -> None:
        self._layers_by_raster: dict[str, TileLayerRecord] = {}
        self._layers_by_id: dict[str, TileLayerRecord] = {}
        self._raster_repository = raster_repository
        self._scene_repository = scene_repository

    def upsert_for_raster(self, record: TileLayerRecord) -> TileLayerRecord:
        existing = self._layers_by_raster.get(record.raster_output_id)
        record.layer_id = existing.layer_id if existing else record.layer_id or _layer_id(record)
        self._layers_by_raster[record.raster_output_id] = record
        self._layers_by_id[record.layer_id] = record
        return record

    def get(self, layer_id: str) -> TileLayerRecord | None:
        return self._layers_by_id.get(layer_id)

    def get_with_raster(self, layer_id: str) -> TileLayerResolution | None:
        layer = self._layers_by_id.get(layer_id)
        if layer is None:
            return None
        if self._raster_repository is None:
            return None
        raster = self._raster_repository.get(layer.raster_output_id)
        if raster is None:
            return None
        item_id: str | None = None
        if self._scene_repository is not None:
            scene = self._scene_repository.get(raster.scene_id)
            item_id = scene.pgstac_item_id if scene is not None else None
        return _build_resolution(layer, raster, item_id)


class DatabaseTileLayerRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_for_raster(self, record: TileLayerRecord) -> TileLayerRecord:
        layer_id = record.layer_id or _layer_id(record)
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.tile_layers (
                        layer_id,
                        raster_output_id,
                        visibility,
                        metadata
                    )
                    VALUES (
                        :layer_id,
                        CAST(:raster_output_id AS uuid),
                        :visibility,
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (layer_id) DO UPDATE
                    SET raster_output_id = EXCLUDED.raster_output_id,
                        visibility = EXCLUDED.visibility,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    RETURNING *
                    """
                ),
                {
                    "layer_id": layer_id,
                    "raster_output_id": record.raster_output_id,
                    "visibility": record.visibility,
                    "metadata": dumps(record.metadata, sort_keys=True),
                },
            ).mappings().one()
        return _row_to_layer(row)

    def get(self, layer_id: str) -> TileLayerRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM akasha.tile_layers WHERE layer_id = :layer_id"),
                {"layer_id": layer_id},
            ).mappings().first()
        return _row_to_layer(row) if row else None

    def get_with_raster(self, layer_id: str) -> TileLayerResolution | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        tl.layer_id AS layer_id,
                        tl.raster_output_id AS raster_output_id,
                        tl.visibility AS visibility,
                        ro.object_path AS object_path,
                        ro.index_name AS index_name,
                        ro.scene_id AS scene_id,
                        ro.min_value AS min_value,
                        ro.max_value AS max_value,
                        ro.metadata AS raster_metadata,
                        ps.pgstac_item_id AS pgstac_item_id
                    FROM akasha.tile_layers tl
                    JOIN akasha.raster_outputs ro ON ro.id = tl.raster_output_id
                    LEFT JOIN akasha.provider_scenes ps ON ps.id = ro.scene_id
                    WHERE tl.layer_id = :layer_id
                    """
                ),
                {"layer_id": layer_id},
            ).mappings().first()
        if row is None:
            return None
        raster_metadata = dict(row["raster_metadata"] or {})
        return TileLayerResolution(
            layer_id=row["layer_id"],
            raster_output_id=str(row["raster_output_id"]),
            visibility=row["visibility"],
            object_path=row["object_path"],
            index_name=row["index_name"],
            scene_id=str(row["scene_id"]),
            collection_id=str(
                raster_metadata.get("pgstac_collection") or DEFAULT_DERIVED_COLLECTION_ID
            ),
            asset_key=str(raster_metadata.get("pgstac_asset_key") or row["index_name"] or ""),
            item_id=row["pgstac_item_id"],
            min_value=float(row["min_value"]) if row["min_value"] is not None else None,
            max_value=float(row["max_value"]) if row["max_value"] is not None else None,
        )


def _layer_id(record: TileLayerRecord) -> str:
    material = f"{record.raster_output_id}:{record.visibility}"
    return f"lyr_{sha256(material.encode()).hexdigest()[:24]}"


def _build_resolution(
    layer: TileLayerRecord,
    raster: Any,
    item_id: str | None,
) -> TileLayerResolution:
    raster_metadata = dict(getattr(raster, "metadata", {}) or {})
    return TileLayerResolution(
        layer_id=layer.layer_id or "",
        raster_output_id=layer.raster_output_id,
        visibility=layer.visibility,
        object_path=raster.object_path,
        index_name=raster.index_name,
        scene_id=raster.scene_id,
        collection_id=str(
            raster_metadata.get("pgstac_collection") or DEFAULT_DERIVED_COLLECTION_ID
        ),
        asset_key=str(raster_metadata.get("pgstac_asset_key") or raster.index_name or ""),
        item_id=item_id,
        min_value=raster.min_value,
        max_value=raster.max_value,
    )


def _row_to_layer(row: Any) -> TileLayerRecord:
    return TileLayerRecord(
        layer_id=row.layer_id,
        raster_output_id=str(row.raster_output_id),
        visibility=row.visibility,
        metadata=dict(row.metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
