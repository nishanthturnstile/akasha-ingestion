from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import h5py

from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.pgstac_repository import build_nisar_backscatter_item
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import RuntimeBackend, Settings, validate_resourcesat_runtime_roots
from akasha.jobs.idempotency import compute_nisar_backfill_idempotency_key
from akasha.jobs.stage_store import JobStage
from akasha.jobs.store import Job, JobStatus
from akasha.processing.nisar import (
    NISAR_COLLECTION_ID,
    NISAR_NODATA,
    NISAR_PROCESSING_PROFILE_VERSION,
    NISAR_PROVIDER_ROUTE,
    NISAR_SOURCE_ID,
    PreparedNisarScene,
    SelectedNisarProduct,
    prepare_nisar_product,
)
from akasha.providers.bhoonidhi import BhoonidhiCandidate, BhoonidhiClient, redact_string
from akasha.schemas import SyncRequest
from akasha.storage.object_store import file_sha256

NISAR_BACKFILL_TASK = "akasha.jobs.nisar_tasks.backfill"
NISAR_STAGE_NAMES = (
    "provider_search",
    "raw_download",
    "prepare_scene",
    "scene_validation",
    "object_storage",
    "pgstac_registration",
    "cleanup",
)
NISAR_MODE_LIMIT = {
    "metadata_only": "provider_search",
    "download_only": "raw_download",
    "prepare_only": "scene_validation",
    "full_pipeline": "cleanup",
}
PrepareNisarCallable = Callable[..., PreparedNisarScene]


@dataclass(slots=True)
class NisarBackfillSummary:
    source_id: str
    provider_route: str
    aoi_id: str
    date_start: date
    date_end: date
    mode: str
    searched_count: int = 0
    selected_count: int = 0
    downloaded_count: int = 0
    prepared_count: int = 0
    registered_count: int = 0
    product_ids: list[str] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)
    candidate_evidence: list[dict[str, object]] = field(default_factory=list)
    download_evidence: list[dict[str, object]] = field(default_factory=list)

    def metadata(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "provider_route": self.provider_route,
            "aoi_id": self.aoi_id,
            "date_start": self.date_start.isoformat(),
            "date_end": self.date_end.isoformat(),
            "mode": self.mode,
            "searched_count": self.searched_count,
            "selected_count": self.selected_count,
            "downloaded_count": self.downloaded_count,
            "prepared_count": self.prepared_count,
            "registered_count": self.registered_count,
            "product_ids": list(self.product_ids),
            "item_ids": list(self.item_ids),
            "candidate_evidence": list(self.candidate_evidence),
            "download_evidence": list(self.download_evidence),
        }


@dataclass(frozen=True, slots=True)
class _DownloadedProduct:
    candidate: BhoonidhiCandidate
    path: Path
    object_path: str
    checksum_sha256: str
    package_format: str


class NisarIngestionService:
    def __init__(
        self,
        *,
        job_store: Any,
        stage_store: Any,
        settings: Settings,
        aoi_repository: Any,
        object_store: Any,
        bhoonidhi_client: BhoonidhiClient,
        scene_repository: Any,
        asset_repository: Any,
        pgstac_repository: Any | None = None,
        source_provider_route_repository: Any | None = None,
        task_dispatcher: Callable[[str, str, str], None] | None = None,
        prepare_product: PrepareNisarCallable = prepare_nisar_product,
    ) -> None:
        self._job_store = job_store
        self._stage_store = stage_store
        self._settings = settings
        self._aoi_repository = aoi_repository
        self._object_store = object_store
        self._bhoonidhi_client = bhoonidhi_client
        self._scene_repository = scene_repository
        self._asset_repository = asset_repository
        self._pgstac_repository = pgstac_repository
        self._source_provider_route_repository = source_provider_route_repository
        self._task_dispatcher = task_dispatcher
        self._prepare_product = prepare_product

    def start_backfill(self, request: SyncRequest) -> Job:
        if request.job_type != "nisar_backfill":
            raise ValueError("NisarIngestionService only handles nisar_backfill")
        provider_route = request.provider_route or NISAR_PROVIDER_ROUTE
        key = compute_nisar_backfill_idempotency_key(
            source_id=request.source_id,
            provider_route=provider_route,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
            mode=request.mode,
            request_params_version=self._settings.request_params_version,
            processing_profile_version=NISAR_PROCESSING_PROFILE_VERSION,
        )
        job, created = self._job_store.create_or_get(
            job_type=request.job_type,
            idempotency_key=key,
            source_id=request.source_id,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
        )
        if not created:
            return job
        if self._settings.task_always_eager:
            return self.execute_backfill(
                job.job_id, mode=request.mode, provider_route=provider_route
            )
        if self._task_dispatcher is not None:
            self._task_dispatcher(job.job_id, request.mode, provider_route)
            return job
        from akasha.jobs.celery_app import celery_app

        try:
            celery_app.send_task(
                NISAR_BACKFILL_TASK, args=[job.job_id, request.mode, provider_route]
            )
        except Exception as exc:
            self._job_store.mark_failed(job, error=f"task dispatch failed: {exc}")
            raise
        return job

    def execute_backfill(
        self,
        job_id: str,
        *,
        mode: str = "metadata_only",
        provider_route: str = NISAR_PROVIDER_ROUTE,
    ) -> Job:
        if mode not in NISAR_MODE_LIMIT:
            raise ValueError(f"unsupported NISAR backfill mode: {mode}")
        job = self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        if job.status == JobStatus.COMPLETED:
            return job
        started_at = datetime.now(UTC)
        self._job_store.mark_running(job)
        try:
            self._require_dependencies(mode)
            aoi = self._aoi_repository.get(job.aoi_id)
            if aoi is None:
                raise ValueError(f"AOI not found: {job.aoi_id}")
            summary = NisarBackfillSummary(
                source_id=job.source_id,
                provider_route=provider_route,
                aoi_id=job.aoi_id,
                date_start=date.fromisoformat(job.date_start),
                date_end=date.fromisoformat(job.date_end),
                mode=mode,
            )
            candidates = self._search(job, aoi, summary)
            if not self._reaches(mode, "raw_download"):
                return self._complete(job, summary, started_at)
            downloads = self._download(job, candidates, summary)
            if not self._reaches(mode, "prepare_scene"):
                return self._complete(job, summary, started_at)
            prepared = self._prepare(job, downloads, summary)
            with self._stage(job, "scene_validation", {"prepared_count": len(prepared)}):
                summary.prepared_count = len(prepared)
            if not self._reaches(mode, "object_storage"):
                return self._complete(job, summary, started_at)
            with self._stage(job, "object_storage", {"prepared_count": len(prepared)}) as stage:
                registered = [
                    self._store_and_register(job, download, scene)
                    for download, scene in zip(downloads, prepared, strict=True)
                ]
                stage.metadata["stored_count"] = len(registered)
            with self._stage(job, "pgstac_registration", {"scene_count": len(registered)}) as stage:
                item_ids = [self._register_pgstac(scene, asset) for scene, asset in registered]
                summary.item_ids = item_ids
                summary.registered_count = len(item_ids)
                stage.metadata["item_ids"] = item_ids
            with self._stage(job, "cleanup", {"durable_objects_retained": True}) as stage:
                stage.metadata["removed_local_paths"] = self._cleanup_local_artifacts(
                    job, prepared
                )
            return self._complete(job, summary, started_at)
        except Exception as exc:
            self._job_store.mark_failed(job, error=redact_string(str(exc)))
            raise

    def recover_worker_lost(self, job_id: str) -> None:
        job = self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        for stage in self._stage_store.list_for_job(job_id):
            if stage.status.value == "running":
                self._stage_store.mark_failed(
                    stage.stage_id,
                    error_code="worker_lost",
                    error_message="Celery redelivered the NISAR task after worker exit.",
                )
        self._job_store.mark_queued(job)

    def _search(
        self, job: Job, aoi: Any, summary: NisarBackfillSummary
    ) -> list[BhoonidhiCandidate]:
        with self._stage(job, "provider_search", {"collection": NISAR_COLLECTION_ID}) as stage:
            candidates = self._bhoonidhi_client.search(
                source_id=NISAR_SOURCE_ID,
                collection=NISAR_COLLECTION_ID,
                intersects=aoi.geometry,
                aoi_bbox=aoi.bbox,
                date_start=summary.date_start,
                date_end=summary.date_end,
                max_items=self._settings.backfill_search_item_cap,
            )
            eligible = [item for item in candidates if item.online and item.intersects_aoi]
            selected = sorted(
                eligible,
                key=lambda item: (
                    item.overlap_area,
                    item.acquisition_at or datetime.min.replace(tzinfo=UTC),
                    item.provider_product_id,
                ),
                reverse=True,
            )[: self._settings.nisar_max_downloads_per_run]
            summary.searched_count = len(candidates)
            summary.selected_count = len(selected)
            summary.product_ids = [item.provider_product_id for item in selected]
            summary.candidate_evidence = [
                {
                    "provider_product_id": item.provider_product_id,
                    "acquisition_datetime": item.acquisition_datetime,
                    "bbox": list(item.bbox),
                    "online": item.online,
                    "intersects_aoi": item.intersects_aoi,
                    "selected": item in selected,
                }
                for item in candidates[: self._settings.backfill_search_item_cap]
            ]
            stage.metadata.update(
                {
                    "searched_count": len(candidates),
                    "selected_product_ids": summary.product_ids,
                    "candidate_evidence": summary.candidate_evidence,
                }
            )
            return selected

    def _download(
        self,
        job: Job,
        candidates: list[BhoonidhiCandidate],
        summary: NisarBackfillSummary,
    ) -> list[_DownloadedProduct]:
        downloads: list[_DownloadedProduct] = []
        with self._stage(job, "raw_download", {"candidate_count": len(candidates)}) as stage:
            for candidate in candidates:
                path = (
                    Path(self._settings.scratch_dir)
                    / "nisar-downloads"
                    / job.job_id
                    / _safe_component(candidate.provider_product_id)
                    / "original.zip"
                )
                result = self._bhoonidhi_client.download_product(
                    product_id=candidate.provider_product_id,
                    collection=NISAR_COLLECTION_ID,
                    destination=path,
                )
                downloaded_path = Path(str(result["path"]))
                checksum = str(result.get("sha256") or file_sha256(downloaded_path))
                package_format = _nisar_package_format(downloaded_path)
                object_path, checksum = self._object_store.put_raw_file(
                    provider="bhoonidhi",
                    source_id=NISAR_SOURCE_ID,
                    product_id=candidate.provider_product_id,
                    file_path=downloaded_path,
                    checksum_sha256=checksum,
                    metadata={
                        "provider-route": NISAR_PROVIDER_ROUTE,
                        "source-format": package_format,
                    },
                )
                downloads.append(
                    _DownloadedProduct(
                        candidate,
                        downloaded_path,
                        object_path,
                        checksum,
                        package_format,
                    )
                )
            summary.downloaded_count = len(downloads)
            summary.download_evidence = [
                {
                    "provider_product_id": download.candidate.provider_product_id,
                    "archive_size_bytes": download.path.stat().st_size,
                    "checksum_sha256": download.checksum_sha256,
                    "package_format": download.package_format,
                }
                for download in downloads
            ]
            stage.metadata.update(
                {
                    "downloaded_count": len(downloads),
                    "download_evidence": summary.download_evidence,
                }
            )
        return downloads

    def _prepare(
        self,
        job: Job,
        downloads: list[_DownloadedProduct],
        summary: NisarBackfillSummary,
    ) -> list[PreparedNisarScene]:
        prepared: list[PreparedNisarScene] = []
        with self._stage(job, "prepare_scene", {"downloaded_count": len(downloads)}) as stage:
            for download in downloads:
                candidate = download.candidate
                prepared.append(
                    self._prepare_product(
                        SelectedNisarProduct(
                            product_id=candidate.provider_product_id,
                            package_path=download.path,
                            acquisition_at=candidate.acquisition_at,
                            aoi_id=job.aoi_id,
                            bbox=candidate.bbox,
                            geometry=_candidate_geometry(candidate),
                            provider_metadata={
                                **candidate.provider_metadata,
                                "provider_properties": candidate.provider_properties,
                            },
                        ),
                        self._settings,
                    )
                )
            summary.prepared_count = len(prepared)
            stage.metadata["prepared_count"] = len(prepared)
        return prepared

    def _store_and_register(
        self,
        job: Job,
        download: _DownloadedProduct,
        prepared: PreparedNisarScene,
    ) -> tuple[ProviderSceneRecord, SceneAssetRecord]:
        object_path, checksum = self._object_store.put_prepared_cog_file(
            provider="bhoonidhi",
            source_id=NISAR_SOURCE_ID,
            product_id=prepared.product_id,
            asset_key="backscatter",
            file_path=prepared.backscatter_path,
            checksum_sha256=prepared.checksum_sha256,
            metadata={"processing-profile": NISAR_PROCESSING_PROFILE_VERSION},
        )
        route_id = None
        if self._source_provider_route_repository is not None:
            route = self._source_provider_route_repository.get_by_route_key(
                NISAR_SOURCE_ID, NISAR_PROVIDER_ROUTE
            )
            route_id = route.id if route else None
        identification = dict(prepared.manifest["identification"])
        scene = self._scene_repository.upsert(
            ProviderSceneRecord(
                id=None,
                provider_adapter="bhoonidhi",
                source_id=NISAR_SOURCE_ID,
                provider_product_id=prepared.product_id,
                acquisition_at=prepared.acquisition_at,
                scene_geometry=prepared.geometry,
                status="accepted",
                license_state="restricted",
                provider_metadata={
                    "provider_collection": NISAR_COLLECTION_ID,
                    "polarizations": list(prepared.polarizations),
                    "processing_family": "sar_backscatter",
                    "input_representation": "float32_gamma0_power",
                    "calibration_formula": "10*log10(gamma0_power)",
                    "output_scale": "db",
                    "bbox": prepared.bbox,
                    "identification": identification,
                },
                aoi_id=job.aoi_id,
                provider_route_id=route_id,
                logical_scene_key=f"{NISAR_SOURCE_ID}:{prepared.product_id}",
                native_crs=prepared.crs,
                native_resolution=prepared.resolution,
                raw_object_path=download.object_path,
                file_size_bytes=download.path.stat().st_size,
            )
        )
        asset = self._asset_repository.upsert(
            SceneAssetRecord(
                id=None,
                scene_id=scene.id or "",
                asset_kind="sar_backscatter",
                asset_key="backscatter",
                object_path=object_path,
                asset_href=f"s3://{self._settings.minio_bucket}/{object_path}",
                checksum_sha256=checksum,
                size_bytes=prepared.backscatter_path.stat().st_size,
                storage_backend="minio",
                nodata_value=NISAR_NODATA,
                roles=["data", "backscatter"],
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                metadata={
                    "polarizations": list(prepared.polarizations),
                    "unit": "dB",
                    "processing_profile_version": NISAR_PROCESSING_PROFILE_VERSION,
                    "calibration_formula": "10*log10(gamma0_power)",
                    "rtc_applied": True,
                    "frequency_band": "S",
                    "identification": identification,
                    "bbox": prepared.bbox,
                    "geometry": prepared.geometry,
                    "crs": prepared.crs,
                    "resolution": prepared.resolution,
                },
            )
        )
        return scene, asset

    def _register_pgstac(self, scene: ProviderSceneRecord, asset: SceneAssetRecord) -> str:
        item = build_nisar_backscatter_item(
            scene=scene,
            asset=asset,
            bbox=[float(value) for value in asset.metadata["bbox"]],
            geometry=dict(asset.metadata["geometry"]),
        )
        scene.pgstac_item_id = item.id
        self._scene_repository.upsert(scene)
        if self._pgstac_repository is not None:
            self._pgstac_repository.upsert_item_json(item)
        return item.id

    def _cleanup_local_artifacts(
        self,
        job: Job,
        prepared: list[PreparedNisarScene],
    ) -> list[str]:
        scratch = Path(self._settings.scratch_dir).resolve()
        targets = {
            scratch / "nisar-downloads" / job.job_id,
            *(
                scratch / "nisar-prepare" / _safe_component(scene.product_id)
                for scene in prepared
            ),
        }
        removed: list[str] = []
        for target in sorted(targets):
            resolved = target.resolve()
            if scratch not in resolved.parents or not resolved.exists():
                continue
            shutil.rmtree(resolved)
            removed.append(str(resolved.relative_to(scratch)))
        return removed

    def _require_dependencies(self, mode: str) -> None:
        if self._aoi_repository is None:
            raise ValueError("NISAR backfill requires an AOI repository")
        if self._settings.runtime_backend != RuntimeBackend.MEMORY:
            if (
                self._settings.bhoonidhi_approved_runtime_required
                and not self._settings.bhoonidhi_approved_runtime
            ):
                raise ValueError("Bhoonidhi NISAR live jobs require approved runtime")
            validate_resourcesat_runtime_roots(self._settings, dry_run=False)
            required = self._settings.source_mirror_required_headroom_bytes
            available = shutil.disk_usage(_disk_usage_path(self._settings.scratch_dir)).free
            if required > 0 and available < required:
                raise ValueError("insufficient NISAR disk headroom")
        if self._reaches(mode, "raw_download") and self._object_store is None:
            raise ValueError("NISAR download requires object storage")
        if self._reaches(mode, "prepare_scene") and (
            self._scene_repository is None or self._asset_repository is None
        ):
            raise ValueError("NISAR preparation requires scene and asset repositories")

    def _reaches(self, mode: str, stage: str) -> bool:
        return NISAR_STAGE_NAMES.index(stage) <= NISAR_STAGE_NAMES.index(NISAR_MODE_LIMIT[mode])

    def _complete(
        self, job: Job, summary: NisarBackfillSummary, started_at: datetime
    ) -> Job:
        metadata = summary.metadata()
        metadata["duration_seconds"] = (datetime.now(UTC) - started_at).total_seconds()
        return self._job_store.mark_completed(
            job,
            result_metadata={
                "backfill_summary": metadata,
                "mode": summary.mode,
                "provider_route": summary.provider_route,
            },
        )

    def _stage(self, job: Job, name: str, metadata: dict[str, Any]) -> _StageContext:
        return _StageContext(self._stage_store, job, name, metadata)


class _StageContext:
    def __init__(self, store: Any, job: Job, name: str, metadata: dict[str, Any]) -> None:
        self._store = store
        self._job = job
        self._name = name
        self._metadata = metadata
        self.stage: JobStage | None = None

    def __enter__(self) -> JobStage:
        self.stage = self._store.start_stage(
            job_id=self._job.job_id, stage_name=self._name, metadata=self._metadata
        )
        return self.stage

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.stage is None:
            return
        if exc is None:
            self._store.mark_completed(self.stage.stage_id, metadata=self.stage.metadata)
        else:
            self._store.mark_failed(
                self.stage.stage_id,
                error_code="nisar_stage_failed",
                error_message=redact_string(str(exc)),
                metadata=self.stage.metadata,
            )


def _candidate_geometry(candidate: BhoonidhiCandidate) -> dict[str, Any]:
    geometry = candidate.raw_item.get("geometry")
    if isinstance(geometry, dict):
        return geometry
    west, south, east, north = candidate.bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def _safe_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    return safe[:120] or "nisar"


def _nisar_package_format(path: Path) -> str:
    if h5py.is_hdf5(path):
        return "direct_hdf5"
    if zipfile.is_zipfile(path):
        return "zip"
    raise ValueError("Bhoonidhi NISAR download is neither HDF5 nor ZIP")


def _disk_usage_path(path: Path) -> Path:
    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    return current
