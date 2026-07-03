from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import pystac
from sqlalchemy import Engine, text

from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord

PHASE2_DERIVED_COLLECTION_ID = "akasha-sentinel-2-l2a-derived-v1"


class PgstacRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_item_json(self, item: pystac.Item) -> None:
        item_dict = item.to_dict()
        collection = _collection(item.collection_id or PHASE2_DERIVED_COLLECTION_ID)
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pgstac.upsert_collection(CAST(:collection AS jsonb))"),
                {"collection": _json_dumps(collection)},
            )
            connection.execute(
                text("SELECT pgstac.upsert_item(CAST(:item AS jsonb))"),
                {"item": _json_dumps(item_dict)},
            )


def build_derived_item(
    *,
    scene: ProviderSceneRecord,
    outputs: list[RasterOutputRecord],
    bbox: list[float],
    geometry: dict[str, Any],
) -> pystac.Item:
    item_id = scene.pgstac_item_id or _derived_item_id(scene)
    item = pystac.Item(
        id=item_id,
        geometry=geometry,
        bbox=bbox,
        datetime=scene.acquisition_at or datetime.now(UTC),
        properties={
            "akasha:source_id": scene.source_id,
            "akasha:provider_adapter": scene.provider_adapter,
            "akasha:logical_scene_key": scene.logical_scene_key,
        },
    )
    item.collection_id = PHASE2_DERIVED_COLLECTION_ID
    for output in outputs:
        if output.index_name is None:
            continue
        item.add_asset(
            output.index_name,
            pystac.Asset(
                href=str(output.metadata.get("pgstac_href") or output.object_path),
                media_type=pystac.MediaType.COG,
                roles=["data"],
                extra_fields={
                    "akasha:formula_version": output.formula_version,
                    "akasha:processing_profile_version": output.processing_profile_version,
                },
            ),
        )
    return item


def _derived_item_id(scene: ProviderSceneRecord) -> str:
    logical = scene.logical_scene_key or scene.provider_product_id
    acquisition = (
        scene.acquisition_at.strftime("%Y%m%dT%H%M%S") if scene.acquisition_at else "unknown"
    )
    product_hash = sha256(logical.encode()).hexdigest()[:12]
    mgrs_or_group = str(scene.provider_metadata.get("mgrs_tile") or "group")
    return f"s2-l2a-{mgrs_or_group}-{acquisition}-{product_hash}"


def _json_dumps(value: dict[str, Any]) -> str:
    from json import dumps

    return dumps(value, sort_keys=True)


def _collection(collection_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "description": "Akasha Sentinel-2 L2A derived vegetation index COGs.",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[now, None]]},
        },
        "links": [],
    }
