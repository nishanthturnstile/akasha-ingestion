from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from akasha.catalog.aoi_repository import AoiRecord
from akasha.catalog.asset_repository import InMemorySceneAssetRepository
from akasha.catalog.scene_repository import InMemorySceneRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs.stage_store import InMemoryStageStore, StageStatus
from akasha.jobs.store import InMemoryJobStore
from akasha.processing.nisar import NISAR_PROVIDER_ROUTE, NISAR_SOURCE_ID, PreparedNisarScene
from akasha.providers.bhoonidhi import BhoonidhiCandidate
from akasha.scheduler.source_registry import nisar_source_state
from akasha.schemas import SyncRequest
from akasha.services.nisar_ingestion import NisarIngestionService
from akasha.storage.object_store import InMemoryObjectStore


def test_nisar_source_is_hidden_manual_and_pending_real_product_validation() -> None:
    source = nisar_source_state(Settings(environment=Environment.TEST))

    assert source.source_id == NISAR_SOURCE_ID
    assert source.lifecycle_state == "manual"
    assert source.schedule_state == "manual"
    assert source.product_exposure == "hidden"
    assert source.validation_state == "pending"
    assert source.default_aois[0].max_downloads == 1


def test_nisar_backfill_requires_exact_source_and_provider_route() -> None:
    common = {
        "source_id": NISAR_SOURCE_ID,
        "aoi_id": "bangalore",
        "date_start": date(2026, 7, 1),
        "date_end": date(2026, 7, 19),
        "job_type": "nisar_backfill",
        "mode": "metadata_only",
    }

    with pytest.raises(ValidationError, match="provider_route"):
        SyncRequest(**common, provider_route="bhoonidhi:wrong")
    with pytest.raises(ValidationError, match="source_id"):
        SyncRequest(
            **{**common, "source_id": "eos-04-sar-mrs-l2b"},
            provider_route=NISAR_PROVIDER_ROUTE,
        )


def test_nisar_full_pipeline_is_capped_idempotent_and_catalogs_backscatter(
    tmp_path: Path,
) -> None:
    job_store = InMemoryJobStore()
    stage_store = InMemoryStageStore()
    scenes = InMemorySceneRepository()
    assets = InMemorySceneAssetRepository()
    pgstac = _PgstacRepository()
    service = NisarIngestionService(
        job_store=job_store,
        stage_store=stage_store,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=True,
            scratch_dir=tmp_path,
            nisar_max_downloads_per_run=1,
        ),
        aoi_repository=_AoiRepository(),
        object_store=InMemoryObjectStore(),
        bhoonidhi_client=_BhoonidhiClient(),
        scene_repository=scenes,
        asset_repository=assets,
        pgstac_repository=pgstac,
        prepare_product=_prepare(tmp_path),
    )
    request = SyncRequest(
        source_id=NISAR_SOURCE_ID,
        provider_route=NISAR_PROVIDER_ROUTE,
        aoi_id="bangalore",
        date_start=date(2026, 7, 1),
        date_end=date(2026, 7, 19),
        job_type="nisar_backfill",
        mode="full_pipeline",
    )

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
    assert summary["candidate_evidence"] == [
        {
            "provider_product_id": "NISAR-GRAZING",
            "acquisition_datetime": "2026-07-12T05:30:00Z",
            "bbox": [77.0, 12.0, 78.0, 13.0],
            "online": True,
            "intersects_aoi": True,
            "selected": False,
        },
        {
            "provider_product_id": "NISAR-COVERING",
            "acquisition_datetime": "2026-07-12T05:30:00Z",
            "bbox": [77.0, 12.0, 78.0, 13.0],
            "online": True,
            "intersects_aoi": True,
            "selected": True,
        },
    ]
    assert summary["download_evidence"] == [
        {
            "provider_product_id": "NISAR-COVERING",
            "archive_size_bytes": len(b"zip:NISAR-COVERING"),
            "checksum_sha256": sha256(b"zip:NISAR-COVERING").hexdigest(),
        }
    ]
    scene = scenes.list_for_source_aoi(source_id=NISAR_SOURCE_ID, aoi_id="bangalore")[0]
    assert scene.provider_product_id == "NISAR-COVERING"
    assert scene.pgstac_item_id == summary["item_ids"][0]
    asset = assets.list_for_scene(scene.id or "")[0]
    assert asset.asset_key == "backscatter"
    assert asset.metadata["polarizations"] == ["HH", "HV"]
    assert asset.metadata["frequency_band"] == "S"
    assert pgstac.items[0].collection_id == "akasha-nisar-ssar-beta-gcov-backscatter-v1"
    assert pgstac.items[0].properties["sar:frequency_band"] == "S"
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


def test_nisar_stage_failure_is_redacted_and_worker_loss_is_recoverable(
    tmp_path: Path,
) -> None:
    job_store = InMemoryJobStore()
    stage_store = InMemoryStageStore()
    job, _ = job_store.create_or_get(
        job_type="nisar_backfill",
        idempotency_key="nisar-failure",
        source_id=NISAR_SOURCE_ID,
        aoi_id="bangalore",
        date_start="2026-07-01",
        date_end="2026-07-19",
    )
    service = NisarIngestionService(
        job_store=job_store,
        stage_store=stage_store,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            scratch_dir=tmp_path,
        ),
        aoi_repository=_AoiRepository(),
        object_store=None,
        bhoonidhi_client=_FailingBhoonidhiClient(),
        scene_repository=None,
        asset_repository=None,
    )

    with pytest.raises(RuntimeError, match="X-Amz-Signature"):
        service.execute_backfill(job.job_id, mode="metadata_only")

    assert job.status == "failed"
    assert "secret-token" not in (job.error or "")
    failed_stage = stage_store.list_for_job(job.job_id)[0]
    assert failed_stage.status == StageStatus.FAILED
    assert "secret-token" not in (failed_stage.error_message or "")

    recovery_job, _ = job_store.create_or_get(
        job_type="nisar_backfill",
        idempotency_key="nisar-recovery",
        source_id=NISAR_SOURCE_ID,
        aoi_id="bangalore",
        date_start="2026-07-01",
        date_end="2026-07-19",
    )
    running = stage_store.start_stage(
        job_id=recovery_job.job_id,
        stage_name="prepare_scene",
    )
    job_store.mark_running(recovery_job)
    service.recover_worker_lost(recovery_job.job_id)

    assert recovery_job.status == "queued"
    assert running.status == StageStatus.FAILED
    assert running.error_code == "worker_lost"


class _AoiRepository:
    def get(self, aoi_id: str) -> AoiRecord:
        return AoiRecord(
            aoi_id=aoi_id,
            name="Bangalore",
            geometry=_geometry(),
            bbox=[77.0, 12.0, 78.0, 13.0],
        )


class _BhoonidhiClient:
    def search(self, **kwargs: object) -> list[BhoonidhiCandidate]:
        assert kwargs["source_id"] == NISAR_SOURCE_ID
        assert kwargs["collection"] == "NISAR_SSAR-Beta_GCOV"
        return [_candidate("NISAR-GRAZING", 0.1), _candidate("NISAR-COVERING", 0.9)]

    def download_product(
        self, *, product_id: str, collection: str, destination: Path
    ) -> dict[str, object]:
        assert collection == "NISAR_SSAR-Beta_GCOV"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"zip:{product_id}".encode())
        return {"path": str(destination), "sha256": sha256(destination.read_bytes()).hexdigest()}


class _FailingBhoonidhiClient:
    def search(self, **_kwargs: object) -> list[BhoonidhiCandidate]:
        raise RuntimeError("provider failed?X-Amz-Signature=secret-token")


class _PgstacRepository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def upsert_item_json(self, item: object) -> None:
        self.items.append(item)


def _prepare(root: Path):
    def prepare(product, _settings):
        path = root / "prepared" / product.product_id / "backscatter.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"validated-nisar-cog-fixture")
        return PreparedNisarScene(
            product_id=product.product_id,
            acquisition_at=product.acquisition_at,
            backscatter_path=path,
            checksum_sha256=sha256(path.read_bytes()).hexdigest(),
            polarizations=("HH", "HV"),
            bbox=product.bbox,
            geometry=product.geometry,
            crs="EPSG:32643",
            resolution=20.0,
            manifest={
                "identification": {
                    "track_number": 12,
                    "frame_number": 34,
                    "orbit_pass_direction": "DESCENDING",
                    "product_specification_version": "1.2.1",
                }
            },
        )

    return prepare


def _candidate(product_id: str, overlap_area: float) -> BhoonidhiCandidate:
    return BhoonidhiCandidate(
        source_id=NISAR_SOURCE_ID,
        collection="NISAR_SSAR-Beta_GCOV",
        provider_product_id=product_id,
        item_id=product_id,
        acquisition_datetime="2026-07-12T05:30:00Z",
        acquisition_at=datetime(2026, 7, 12, 5, 30, tzinfo=UTC),
        bbox=[77.0, 12.0, 78.0, 13.0],
        overlap_bbox=[77.0, 12.0, 78.0, 13.0],
        overlap_area=overlap_area,
        online=True,
        intersects_aoi=True,
        raw_item={"geometry": _geometry()},
        provider_metadata={"polarizations": ["HH", "HV"]},
    )


def _geometry() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [
            [[77.0, 12.0], [78.0, 12.0], [78.0, 13.0], [77.0, 13.0], [77.0, 12.0]]
        ],
    }
