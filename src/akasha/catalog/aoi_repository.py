from __future__ import annotations

from dataclasses import dataclass, field
from json import dumps, loads
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text


@dataclass(frozen=True, slots=True)
class AoiRecord:
    aoi_id: str
    name: str
    geometry: dict[str, Any]
    bbox: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemoryAoiRepository:
    def __init__(self, aoi_geojson_path: Path) -> None:
        self._records = _load_geojson(aoi_geojson_path)

    def get(self, aoi_id: str) -> AoiRecord | None:
        return self._records.get(aoi_id)


class DatabaseAoiRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, aoi_id: str) -> AoiRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        aoi_id,
                        name,
                        ST_AsGeoJSON(geometry)::json AS geometry,
                        bbox,
                        metadata
                    FROM akasha.aoi_registry
                    WHERE aoi_id = :aoi_id
                    """
                ),
                {"aoi_id": aoi_id},
            ).mappings().first()
        if row is None:
            return None
        return AoiRecord(
            aoi_id=row.aoi_id,
            name=row.name,
            geometry=dict(row.geometry),
            bbox=[float(value) for value in row.bbox],
            metadata=dict(row.metadata or {}),
        )


def _load_geojson(path: Path) -> dict[str, AoiRecord]:
    resolved = path if path.is_absolute() else Path.cwd() / path
    payload = loads(resolved.read_text(encoding="utf-8"))
    global_bbox = payload.get("bbox", [])
    records: dict[str, AoiRecord] = {}
    for feature in payload.get("features", []):
        properties = dict(feature.get("properties", {}))
        if properties.get("role") not in {"aoi_polygon", "sample_field"}:
            continue
        feature_id = str(feature["id"])
        records[feature_id] = AoiRecord(
            aoi_id=feature_id,
            name=feature_id.replace("_", " ").title(),
            geometry=dict(feature["geometry"]),
            bbox=[float(value) for value in feature.get("bbox", global_bbox)],
            metadata=properties,
        )
    return records


def geometry_json(geometry: dict[str, Any]) -> str:
    return dumps(geometry, sort_keys=True)
