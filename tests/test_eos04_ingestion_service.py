from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

from akasha.catalog.aoi_repository import AoiRecord
from akasha.catalog.asset_repository import InMemorySceneAssetRepository
from akasha.catalog.scene_repository import InMemorySceneRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs.stage_store import InMemoryStageStore, StageStatus
from akasha.jobs.store import InMemoryJobStore
from akasha.processing.eos04 import EOS04_SOURCE_ID, PreparedEos04Scene
from akasha.providers.bhoonidhi import BhoonidhiCandidate
from akasha.scheduler.source_registry import eos04_source_state
from akasha.schemas import SyncRequest
from akasha.services.eos04_ingestion import Eos04IngestionService
from akasha.storage.object_store import InMemoryObjectStore


def test_eos04_runtime_source_is_validated_but_remains_manual_and_hidden() -> None:
    source = eos04_source_state(Settings(environment=Environment.TEST))

    assert source.validation_state == "accepted"
    assert source.readiness_reasons == ()
    assert source.lifecycle_state == "manual"
    assert source.schedule_state == "manual"
    assert source.product_exposure == "hidden"


def test_eos04_full_pipeline_is_bounded_idempotent_and_catalogs_backscatter(
    tmp_path: Path,
) -> None:
    job_store = InMemoryJobStore()
    stage_store = InMemoryStageStore()
    scenes = InMemorySceneRepository()
    assets = InMemorySceneAssetRepository()
    pgstac = _PgstacRepository()
    service = Eos04IngestionService(
        job_store=job_store,
        stage_store=stage_store,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=True,
            scratch_dir=tmp_path,
            eos04_max_downloads_per_run=1,
        ),
        aoi_repository=_AoiRepository(),
        object_store=InMemoryObjectStore(),
        bhoonidhi_client=_BhoonidhiClient(tmp_path),
        scene_repository=scenes,
        asset_repository=assets,
        pgstac_repository=pgstac,
        prepare_product=_prepare(tmp_path),
    )
    request = _request()

    first = service.start_backfill(request)
    second = service.start_backfill(request)

    assert first.job_id == second.job_id
    assert first.status == "completed"
    summary = first.result_metadata["backfill_summary"]
    assert summary["searched_count"] == 2
    assert summary["selected_count"] == 1
    assert summary["downloaded_count"] == 1
    assert summary["prepared_count"] == 1
    assert summary["registered_count"] == 1
    scene = scenes.list_for_source_aoi(source_id=EOS04_SOURCE_ID, aoi_id="bangalore")[0]
    assert scene.status == "accepted"
    assert scene.provider_product_id == "EOS04-COVERING"
    assert scene.pgstac_item_id == summary["item_ids"][0]
    backscatter = assets.list_for_scene(scene.id or "")[0]
    assert backscatter.asset_key == "backscatter"
    assert backscatter.metadata["unit"] == "dB"
    assert backscatter.metadata["polarizations"] == ["VV", "VH"]
    assert pgstac.items[0].id == scene.pgstac_item_id
    stages = stage_store.list_for_job(first.job_id)
    assert {stage.stage_name for stage in stages} == {
        "provider_search",
        "raw_download",
        "prepare_scene",
        "scene_validation",
        "object_storage",
        "pgstac_registration",
        "cleanup",
    }
    assert all(stage.status == StageStatus.COMPLETED for stage in stages)


def _request() -> SyncRequest:
    return SyncRequest(
        source_id=EOS04_SOURCE_ID,
        provider_route="bhoonidhi:EOS-04_SAR-MRS_L2B",
        aoi_id="bangalore",
        date_start=date(2026, 7, 1),
        date_end=date(2026, 7, 18),
        job_type="eos04_backfill",
        mode="full_pipeline",
    )


class _AoiRepository:
    def get(self, aoi_id: str) -> AoiRecord:
        return AoiRecord(
            aoi_id=aoi_id,
            name="Bangalore",
            geometry=_geometry(),
            bbox=[77.0, 12.0, 78.0, 13.0],
        )


class _BhoonidhiClient:
    def __init__(self, root: Path) -> None:
        self.root = root

    def search(self, **kwargs: object) -> list[BhoonidhiCandidate]:
        assert kwargs["source_id"] == EOS04_SOURCE_ID
        assert kwargs["collection"] == "EOS-04_SAR-MRS_L2B"
        return [_candidate("EOS04-GRAZING", 0.1), _candidate("EOS04-COVERING", 0.9)]

    def download_product(
        self, *, product_id: str, collection: str, destination: Path
    ) -> dict[str, object]:
        assert collection == "EOS-04_SAR-MRS_L2B"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"zip:{product_id}".encode())
        return {"path": str(destination), "sha256": sha256(destination.read_bytes()).hexdigest()}


class _PgstacRepository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def upsert_item_json(self, item: object) -> None:
        self.items.append(item)


def _prepare(root: Path):
    def prepare(product, _settings):
        path = root / "prepared" / product.product_id / "backscatter.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"validated-cog-fixture")
        return PreparedEos04Scene(
            product_id=product.product_id,
            acquisition_at=product.acquisition_at,
            backscatter_path=path,
            checksum_sha256=sha256(path.read_bytes()).hexdigest(),
            polarizations=("VV", "VH"),
            bbox=product.bbox,
            geometry=product.geometry,
            crs="EPSG:32643",
            resolution=18.0,
            manifest={
                "input_representation": "uint16_gamma0_dn",
                "calibration_formula": (
                    "10*log10(DN^2-IMAGE_NOISE_BIAS)-KCAL_BETA0_DB"
                ),
                "output_scale": "db",
                "rtc_apply_flag": 1,
                "comparison_metadata": {
                    "policyVersion": "eos04-comparability-v1",
                    "keyHash": "fixture-comparison-key",
                    "complete": True,
                    "orbitState": "DESCENDING",
                    "trackKey": "scene:22",
                    "incidenceAngleDegrees": 37.8,
                    "sensorOrientation": "RIGHT",
                    "rtcApplied": True,
                },
            },
        )

    return prepare


def _candidate(product_id: str, overlap_area: float) -> BhoonidhiCandidate:
    return BhoonidhiCandidate(
        source_id=EOS04_SOURCE_ID,
        collection="EOS-04_SAR-MRS_L2B",
        provider_product_id=product_id,
        item_id=product_id,
        acquisition_datetime="2026-07-11T05:30:00Z",
        acquisition_at=datetime(2026, 7, 11, 5, 30, tzinfo=UTC),
        bbox=[77.0, 12.0, 78.0, 13.0],
        overlap_bbox=[77.0, 12.0, 78.0, 13.0],
        overlap_area=overlap_area,
        online=True,
        intersects_aoi=True,
        raw_item={"geometry": _geometry()},
        provider_metadata={"polarizations": ["VV", "VH"]},
    )


def _geometry() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [
            [[77.0, 12.0], [78.0, 12.0], [78.0, 13.0], [77.0, 13.0], [77.0, 12.0]]
        ],
    }
