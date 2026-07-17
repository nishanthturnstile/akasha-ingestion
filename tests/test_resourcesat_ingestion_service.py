from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from akasha.catalog.aoi_repository import AoiRecord
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs.stage_store import InMemoryStageStore, StageStatus
from akasha.jobs.store import InMemoryJobStore
from akasha.providers.bhoonidhi import BhoonidhiCandidate
from akasha.schemas import SyncRequest
from akasha.services.resourcesat_ingestion import ResourceSatIngestionService
from akasha.storage.object_store import InMemoryObjectStore


def test_resourcesat_metadata_only_backfill_records_summary_and_skipped_stages(
    tmp_path: Path,
) -> None:
    stage_store = InMemoryStageStore()
    service = _service(
        tmp_path,
        stage_store=stage_store,
        candidates=[
            _candidate("RS2A_LISS3_001"),
            _candidate("RS2A_LISS3_OFFLINE", online=False),
        ],
    )

    job = service.start_backfill(_request(mode="metadata_only"))

    assert job.status == "completed"
    summary = job.result_metadata["backfill_summary"]
    assert summary["searched_count"] == 2
    assert summary["selected_count"] == 1
    assert summary["product_ids"] == ["RS2A_LISS3_001"]
    assert summary["processed_count"] == 0
    assert summary["failed_count"] == 0
    assert summary["skipped_count"] == 1

    stages = stage_store.list_for_job(job.job_id)
    assert stages[0].stage_name == "cleanup"
    assert all(stage.status == StageStatus.COMPLETED for stage in stages)
    provider_search = next(stage for stage in stages if stage.stage_name == "provider_search")
    raw_download = next(stage for stage in stages if stage.stage_name == "raw_download")
    assert provider_search.metadata["selected_count"] == 1
    assert raw_download.metadata["skipped"] is True


def test_resourcesat_selection_prioritizes_aoi_overlap_before_recency(
    tmp_path: Path,
) -> None:
    recent_grazing = _candidate("RECENT_GRAZING", overlap_area=0.01)
    older_covering = _candidate("OLDER_COVERING", overlap_area=0.75)
    service = _service(
        tmp_path,
        candidates=[recent_grazing, older_covering],
    )

    job = service.start_backfill(_request(mode="metadata_only"))

    assert job.result_metadata["backfill_summary"]["product_ids"] == ["OLDER_COVERING"]


def test_resourcesat_selection_uses_source_specific_download_cap(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=True,
            scratch_dir=tmp_path,
            bhoonidhi_max_downloads_per_run=1,
            resourcesat_liss3_max_downloads_per_run=2,
        ),
        candidates=[
            _candidate("FIRST", overlap_area=0.75),
            _candidate("SECOND", overlap_area=0.50),
            _candidate("THIRD", overlap_area=0.25),
        ],
    )

    job = service.start_backfill(_request(mode="metadata_only"))

    assert job.result_metadata["backfill_summary"]["product_ids"] == ["FIRST", "SECOND"]


def test_resourcesat_download_only_uploads_raw_zip_and_is_idempotent(tmp_path: Path) -> None:
    object_store = InMemoryObjectStore()
    service = _service(
        tmp_path,
        object_store=object_store,
        candidates=[_candidate("RS2A_LISS3_001")],
    )
    request = _request(mode="download_only")

    first = service.start_backfill(request)
    second = service.start_backfill(request)

    assert first.job_id == second.job_id
    summary = first.result_metadata["backfill_summary"]
    assert summary["downloaded_count"] == 1
    assert summary["failed_count"] == 0
    assert object_store.exists(
        "raw/bhoonidhi/resourcesat-2a-liss3-boa/RS2A_LISS3_001/original.zip"
    )


def test_resourcesat_download_failure_marks_job_failed_and_allows_retry(tmp_path: Path) -> None:
    job_store = InMemoryJobStore()
    service = _service(
        tmp_path,
        job_store=job_store,
        candidates=[_candidate("RS2A_LISS3_001")],
        fail_downloads={"RS2A_LISS3_001"},
    )

    with pytest.raises(RuntimeError, match="raw downloads failed"):
        service.start_backfill(_request(mode="download_only"))

    failed_job = job_store.list()[0]
    assert failed_job.status == "failed"
    assert failed_job.error == "One or more ResourceSat raw downloads failed"

    retry = service.start_backfill(_request(mode="download_only"))
    assert retry.job_id != failed_job.job_id


def test_resourcesat_download_preflight_rejects_unsafe_runtime_root() -> None:
    job_store = InMemoryJobStore()
    service = _service(
        Path("/tmp/akasha"),
        job_store=job_store,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.EXTERNAL,
            task_always_eager=True,
            scratch_dir="/tmp/akasha",
            resourcesat_approved_data_root="/srv/akasha",
            bhoonidhi_approved_runtime=True,
        ),
        candidates=[_candidate("RS2A_LISS3_001")],
    )

    with pytest.raises(ValueError, match="unsafe ResourceSat runtime root"):
        service.start_backfill(_request(mode="download_only"))

    assert job_store.list()[0].status == "failed"


def test_resourcesat_download_requires_approved_runtime_for_external_backend(
    tmp_path: Path,
) -> None:
    blocked_store = InMemoryJobStore()
    blocked = _service(
        tmp_path,
        job_store=blocked_store,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.EXTERNAL,
            task_always_eager=True,
            scratch_dir="/srv/akasha/scratch",
            resourcesat_approved_data_root="/srv/akasha",
            bhoonidhi_approved_runtime=False,
        ),
        candidates=[_candidate("RS2A_LISS3_002")],
    )

    with pytest.raises(ValueError, match="approved runtime"):
        blocked.start_backfill(_request(mode="download_only"))

    assert blocked_store.list()[0].status == "failed"


def test_resourcesat_metadata_only_requires_approved_runtime_before_provider_search(
    tmp_path: Path,
) -> None:
    job_store = InMemoryJobStore()
    service = ResourceSatIngestionService(
        job_store=job_store,
        stage_store=InMemoryStageStore(),
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.EXTERNAL,
            task_always_eager=True,
            scratch_dir="/srv/akasha/scratch",
            resourcesat_approved_data_root="/srv/akasha",
            bhoonidhi_approved_runtime=False,
        ),
        aoi_repository=_AoiRepository(),
        object_store=InMemoryObjectStore(),
        bhoonidhi_client=_FailingSearchClient(),
    )

    with pytest.raises(ValueError, match="approved runtime"):
        service.start_backfill(_request(mode="metadata_only"))

    assert job_store.list()[0].status == "failed"


def test_resourcesat_download_checks_disk_headroom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_store = InMemoryJobStore()
    monkeypatch.setattr(
        "akasha.services.resourcesat_ingestion.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 1})(),
    )
    service = _service(
        tmp_path,
        job_store=job_store,
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.EXTERNAL,
            task_always_eager=True,
            scratch_dir="/srv/akasha/scratch",
            resourcesat_approved_data_root="/srv/akasha",
            bhoonidhi_approved_runtime=True,
            source_mirror_required_headroom_bytes=10_000,
        ),
        candidates=[_candidate("RS2A_LISS3_001")],
    )

    with pytest.raises(ValueError, match="insufficient ResourceSat disk headroom"):
        service.start_backfill(_request(mode="download_only"))

    assert job_store.list()[0].status == "failed"


def _service(
    tmp_path: Path,
    *,
    job_store: InMemoryJobStore | None = None,
    stage_store: InMemoryStageStore | None = None,
    object_store: InMemoryObjectStore | None = None,
    settings: Settings | None = None,
    candidates: list[BhoonidhiCandidate],
    fail_downloads: set[str] | None = None,
) -> ResourceSatIngestionService:
    resolved_settings = settings or Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        task_always_eager=True,
        scratch_dir=tmp_path,
    )
    return ResourceSatIngestionService(
        job_store=job_store or InMemoryJobStore(),
        stage_store=stage_store or InMemoryStageStore(),
        settings=resolved_settings,
        aoi_repository=_AoiRepository(),
        object_store=object_store or InMemoryObjectStore(),
        bhoonidhi_client=_BhoonidhiClient(tmp_path, candidates, fail_downloads or set()),
    )


def _request(*, mode: str) -> SyncRequest:
    return SyncRequest(
        source_id="resourcesat-2a-liss3-boa",
        provider_route="bhoonidhi:ResourceSat-2A_LISS3_BOA",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start=date(2026, 1, 1),
        date_end=date(2026, 1, 31),
        job_type="resourcesat_backfill",
        mode=mode,
    )


class _AoiRepository:
    def get(self, aoi_id: str) -> AoiRecord:
        return AoiRecord(
            aoi_id=aoi_id,
            name="Bangalore 60km",
            geometry=_geometry(),
            bbox=[77.0, 12.0, 78.0, 13.0],
        )


class _BhoonidhiClient:
    def __init__(
        self,
        root: Path,
        candidates: list[BhoonidhiCandidate],
        fail_downloads: set[str],
    ) -> None:
        self._root = root
        self._candidates = candidates
        self._fail_downloads = fail_downloads

    def search(self, **kwargs: object) -> list[BhoonidhiCandidate]:
        assert kwargs["source_id"] == "resourcesat-2a-liss3-boa"
        assert kwargs["collection"] == "ResourceSat-2A_LISS3_BOA"
        assert kwargs["intersects"] == _geometry()
        assert kwargs["aoi_bbox"] == [77.0, 12.0, 78.0, 13.0]
        return self._candidates

    def download_product(
        self,
        *,
        product_id: str,
        collection: str,
        destination: Path,
    ) -> dict[str, object]:
        assert collection == "ResourceSat-2A_LISS3_BOA"
        if product_id in self._fail_downloads:
            self._fail_downloads.remove(product_id)
            raise RuntimeError(f"download failed: {product_id}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"zip:{product_id}".encode())
        return {
            "status": "downloaded",
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": "abc123",
        }


class _FailingSearchClient:
    def search(self, **_kwargs: object) -> list[BhoonidhiCandidate]:
        raise AssertionError("provider search should not be called without approved runtime")


def _candidate(
    product_id: str,
    *,
    online: bool = True,
    overlap_area: float = 1.0,
) -> BhoonidhiCandidate:
    return BhoonidhiCandidate(
        source_id="resourcesat-2a-liss3-boa",
        collection="ResourceSat-2A_LISS3_BOA",
        provider_product_id=product_id,
        item_id=product_id,
        acquisition_datetime="2026-01-15T05:30:00Z",
        acquisition_at=datetime(2026, 1, 15, 5, 30, tzinfo=UTC),
        bbox=[77.0, 12.0, 78.0, 13.0],
        overlap_bbox=[77.0, 12.0, 78.0, 13.0],
        overlap_area=overlap_area,
        online=online,
        intersects_aoi=True,
        raw_item={"geometry": _geometry()},
    )


def _geometry() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [77.0, 12.0],
                [78.0, 12.0],
                [78.0, 13.0],
                [77.0, 13.0],
                [77.0, 12.0],
            ]
        ],
    }
