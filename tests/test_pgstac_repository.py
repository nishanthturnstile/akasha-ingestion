from __future__ import annotations

from datetime import UTC, datetime

from akasha.catalog.pgstac_repository import (
    PHASE2_DERIVED_COLLECTION_ID,
    PgstacRepository,
    build_derived_item,
)
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord


def test_build_derived_item_sets_collection_and_assets() -> None:
    scene = ProviderSceneRecord(
        id="scene-1",
        provider_adapter="earthsearch",
        source_id="sentinel-2-l2a",
        provider_product_id="S2A_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        logical_scene_key="sentinel-2-l2a:S2A_001",
        provider_metadata={"mgrs_tile": "T43PHQ"},
    )
    output = RasterOutputRecord(
        id="output-1",
        scene_id="scene-1",
        output_kind="derived_index",
        index_name="ndvi",
        object_path="indices/earthsearch/sentinel-2-l2a/S2A_001/ndvi.cog.tif",
        formula_version="ndvi-s2-v1",
        processing_profile_version="sentinel2-l2a-earthsearch-v1",
        processing_resolution=10,
        metadata={"pgstac_href": "s3://akasha-data/indices/S2A_001/ndvi.cog.tif"},
    )

    item = build_derived_item(
        scene=scene,
        outputs=[output],
        bbox=[77.0, 12.5, 78.0, 13.5],
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[77.0, 12.5], [78.0, 12.5], [78.0, 13.5], [77.0, 13.5], [77.0, 12.5]]
            ],
        },
    )

    assert item.collection_id == PHASE2_DERIVED_COLLECTION_ID
    assert item.id.startswith("s2-l2a-T43PHQ-20260115T000000-")
    assert item.assets["ndvi"].href == "s3://akasha-data/indices/S2A_001/ndvi.cog.tif"
    assert item.assets["ndvi"].extra_fields["akasha:formula_version"] == "ndvi-s2-v1"


def test_pgstac_repository_uses_supported_upsert_functions() -> None:
    engine = _FakeEngine()
    repository = PgstacRepository(engine)  # type: ignore[arg-type]
    scene = ProviderSceneRecord(
        id="scene-1",
        provider_adapter="earthsearch",
        source_id="sentinel-2-l2a",
        provider_product_id="S2A_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        logical_scene_key="sentinel-2-l2a:S2A_001",
    )
    item = build_derived_item(
        scene=scene,
        outputs=[],
        bbox=[77.0, 12.5, 78.0, 13.5],
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[77.0, 12.5], [78.0, 12.5], [78.0, 13.5], [77.0, 13.5], [77.0, 12.5]]
            ],
        },
    )

    repository.upsert_item_json(item)

    executed_sql = "\n".join(engine.statements)
    assert "pgstac.upsert_collection" in executed_sql
    assert "pgstac.upsert_item" in executed_sql
    assert "pgstac.load_item" not in executed_sql


class _FakeEngine:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def begin(self):
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, engine: _FakeEngine) -> None:
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
        return None

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        del params
        self._engine.statements.append(str(statement))
        return None
