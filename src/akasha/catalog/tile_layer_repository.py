from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from json import dumps
from typing import Any

from sqlalchemy import Engine, text


@dataclass(slots=True)
class TileLayerRecord:
    layer_id: str | None
    raster_output_id: str
    visibility: str = "private"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InMemoryTileLayerRepository:
    def __init__(self) -> None:
        self._layers_by_raster: dict[str, TileLayerRecord] = {}
        self._layers_by_id: dict[str, TileLayerRecord] = {}

    def upsert_for_raster(self, record: TileLayerRecord) -> TileLayerRecord:
        existing = self._layers_by_raster.get(record.raster_output_id)
        record.layer_id = existing.layer_id if existing else record.layer_id or _layer_id(record)
        self._layers_by_raster[record.raster_output_id] = record
        self._layers_by_id[record.layer_id] = record
        return record

    def get(self, layer_id: str) -> TileLayerRecord | None:
        return self._layers_by_id.get(layer_id)


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


def _layer_id(record: TileLayerRecord) -> str:
    material = f"{record.raster_output_id}:{record.visibility}"
    return f"lyr_{sha256(material.encode()).hexdigest()[:24]}"


def _row_to_layer(row: Any) -> TileLayerRecord:
    return TileLayerRecord(
        layer_id=row.layer_id,
        raster_output_id=str(row.raster_output_id),
        visibility=row.visibility,
        metadata=dict(row.metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
