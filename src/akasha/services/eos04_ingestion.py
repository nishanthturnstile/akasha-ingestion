from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.pgstac_repository import build_eos04_backscatter_item
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import RuntimeBackend, Settings, validate_resourcesat_runtime_roots
from akasha.jobs.idempotency import compute_eos04_backfill_idempotency_key
from akasha.jobs.stage_store import JobStage
from akasha.jobs.store import Job, JobStatus
from akasha.processing.eos04 import (
    EOS04_COLLECTION_ID,
    EOS04_NODATA,
    EOS04_PROCESSING_PROFILE_VERSION,
    EOS04_SOURCE_ID,
    PreparedEos04Scene,
    SelectedEos04Product,
    prepare_eos04_product,
)
from akasha.providers.bhoonidhi import BhoonidhiCandidate, BhoonidhiClient, redact_string
from akasha.schemas import SyncRequest
from akasha.storage.object_store import file_sha256

EOS04_BACKFILL_TASK = "akasha.jobs.eos04_tasks.backfill"
EOS04_PROVIDER_ROUTE = f"bhoonidhi:{EOS04_COLLECTION_ID}"
EOS04_STAGE_NAMES = (
    "provider_search",
    "raw_download",
    "prepare_scene",
    "scene_validation",
    "object_storage",
    "pgstac_registration",
    "cleanup",
)
EOS04_MODE_LIMIT = {
    "metadata_only": "provider_search",
    "download_only": "raw_download",
    "prepare_only": "scene_validation",
    "full_pipeline": "cleanup",
}
PrepareEos04Callable = Callable[..., PreparedEos04Scene]


@dataclass(slots=True)
class Eos04BackfillSummary:
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
    failed_count: int = 0
    product_ids: list[str] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)

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
            "failed_count": self.failed_count,
            "product_ids": list(self.product_ids),
            "item_ids": list(self.item_ids),
        }


@dataclass(frozen=True, slots=True)
class _DownloadedProduct:
    candidate: BhoonidhiCandidate
    path: Path
    object_path: str
    checksum_sha256: str


class Eos04IngestionService:
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
        prepare_product: PrepareEos04Callable = prepare_eos04_product,
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
        if request.job_type != "eos04_backfill":
            raise ValueError("Eos04IngestionService only handles eos04_backfill")
        provider_route = request.provider_route or EOS04_PROVIDER_ROUTE
        key = compute_eos04_backfill_idempotency_key(
            source_id=request.source_id,
            provider_route=provider_route,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
            mode=request.mode,
            request_params_version=self._settings.request_params_version,
            processing_profile_version=EOS04_PROCESSING_PROFILE_VERSION,
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
                job.job_id,
                mode=request.mode,
                provider_route=provider_route,
            )
        if self._task_dispatcher is not None:
            self._task_dispatcher(job.job_id, request.mode, provider_route)
            return job
        from akasha.jobs.celery_app import celery_app

        try:
            celery_app.send_task(
                EOS04_BACKFILL_TASK,
                args=[job.job_id, request.mode, provider_route],
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
        provider_route: str = EOS04_PROVIDER_ROUTE,
    ) -> Job:
        if mode not in EOS04_MODE_LIMIT:
            raise ValueError(f"unsupported EOS-04 backfill mode: {mode}")
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
            summary = Eos04BackfillSummary(
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
            with self._stage(job, "cleanup", {"retained_for_audit": True}):
                pass
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
                    error_message="Celery redelivered the EOS-04 task after worker exit.",
                )
        self._job_store.mark_queued(job)

    def _search(
        self,
        job: Job,
        aoi: Any,
        summary: Eos04BackfillSummary,
    ) -> list[BhoonidhiCandidate]:
        with self._stage(job, "provider_search", {"collection": EOS04_COLLECTION_ID}) as stage:
            candidates = self._bhoonidhi_client.search(
                source_id=EOS04_SOURCE_ID,
                collection=EOS04_COLLECTION_ID,
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
                ),
                reverse=True,
            )[: self._settings.eos04_max_downloads_per_run]
            summary.searched_count = len(candidates)
            summary.selected_count = len(selected)
            summary.product_ids = [item.provider_product_id for item in selected]
            stage.metadata.update(
                {"searched_count": len(candidates), "selected_product_ids": summary.product_ids}
            )
            return selected

    def _download(
        self,
        job: Job,
        candidates: list[BhoonidhiCandidate],
        summary: Eos04BackfillSummary,
    ) -> list[_DownloadedProduct]:
        downloads: list[_DownloadedProduct] = []
        with self._stage(job, "raw_download", {"candidate_count": len(candidates)}) as stage:
            for candidate in candidates:
                path = (
                    Path(self._settings.scratch_dir)
                    / "eos04-downloads"
                    / job.job_id
                    / _safe_component(candidate.provider_product_id)
                    / "original.zip"
                )
                result = self._bhoonidhi_client.download_product(
                    product_id=candidate.provider_product_id,
                    collection=EOS04_COLLECTION_ID,
                    destination=path,
                )
                downloaded_path = Path(str(result["path"]))
                checksum = str(result.get("sha256") or file_sha256(downloaded_path))
                object_path, checksum = self._object_store.put_raw_file(
                    provider="bhoonidhi",
                    source_id=EOS04_SOURCE_ID,
                    product_id=candidate.provider_product_id,
                    file_path=downloaded_path,
                    checksum_sha256=checksum,
                    metadata={"provider-route": EOS04_PROVIDER_ROUTE},
                )
                downloads.append(
                    _DownloadedProduct(candidate, downloaded_path, object_path, checksum)
                )
            summary.downloaded_count = len(downloads)
            stage.metadata["downloaded_count"] = len(downloads)
        return downloads

    def _prepare(
        self,
        job: Job,
        downloads: list[_DownloadedProduct],
        summary: Eos04BackfillSummary,
    ) -> list[PreparedEos04Scene]:
        prepared: list[PreparedEos04Scene] = []
        with self._stage(job, "prepare_scene", {"downloaded_count": len(downloads)}) as stage:
            for download in downloads:
                candidate = download.candidate
                metadata = {
                    **candidate.provider_metadata,
                    "provider_properties": candidate.provider_properties,
                }
                prepared.append(
                    self._prepare_product(
                        SelectedEos04Product(
                            product_id=candidate.provider_product_id,
                            package_path=download.path,
                            acquisition_at=candidate.acquisition_at,
                            aoi_id=job.aoi_id,
                            bbox=candidate.bbox,
                            geometry=_candidate_geometry(candidate),
                            provider_metadata=metadata,
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
        prepared: PreparedEos04Scene,
    ) -> tuple[ProviderSceneRecord, SceneAssetRecord]:
        object_path, checksum = self._object_store.put_prepared_cog_file(
            provider="bhoonidhi",
            source_id=EOS04_SOURCE_ID,
            product_id=prepared.product_id,
            asset_key="backscatter",
            file_path=prepared.backscatter_path,
            checksum_sha256=prepared.checksum_sha256,
            metadata={"processing-profile": EOS04_PROCESSING_PROFILE_VERSION},
        )
        route_id = None
        if self._source_provider_route_repository is not None:
            route = self._source_provider_route_repository.get_by_route_key(
                EOS04_SOURCE_ID,
                EOS04_PROVIDER_ROUTE,
            )
            route_id = route.id if route else None
        scene = self._scene_repository.upsert(
            ProviderSceneRecord(
                id=None,
                provider_adapter="bhoonidhi",
                source_id=EOS04_SOURCE_ID,
                provider_product_id=prepared.product_id,
                acquisition_at=prepared.acquisition_at,
                scene_geometry=prepared.geometry,
                status="accepted",
                license_state="restricted",
                provider_metadata={
                    "provider_collection": EOS04_COLLECTION_ID,
                    "polarizations": list(prepared.polarizations),
                    "processing_family": "sar_backscatter",
                    "input_representation": prepared.manifest["input_representation"],
                    "calibration_formula": prepared.manifest["calibration_formula"],
                    "output_scale": "db",
                    "bbox": prepared.bbox,
                    "comparison_metadata": prepared.manifest["comparison_metadata"],
                    "comparison_key_hash": prepared.manifest["comparison_metadata"]["keyHash"],
                },
                aoi_id=job.aoi_id,
                provider_route_id=route_id,
                logical_scene_key=f"{EOS04_SOURCE_ID}:{prepared.product_id}",
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
                nodata_value=EOS04_NODATA,
                roles=["data", "backscatter"],
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                metadata={
                    "polarizations": list(prepared.polarizations),
                    "unit": "dB",
                    "processing_profile_version": EOS04_PROCESSING_PROFILE_VERSION,
                    "calibration_formula": prepared.manifest["calibration_formula"],
                    "rtc_applied": prepared.manifest["rtc_apply_flag"] == 1,
                    "comparison_policy_version": prepared.manifest["comparison_metadata"][
                        "policyVersion"
                    ],
                    "comparison_key_hash": prepared.manifest["comparison_metadata"]["keyHash"],
                    "bbox": prepared.bbox,
                    "geometry": prepared.geometry,
                    "crs": prepared.crs,
                    "resolution": prepared.resolution,
                },
            )
        )
        return scene, asset

    def _register_pgstac(self, scene: ProviderSceneRecord, asset: SceneAssetRecord) -> str:
        bbox = [float(value) for value in asset.metadata["bbox"]]
        geometry = dict(asset.metadata["geometry"])
        item = build_eos04_backscatter_item(
            scene=scene,
            asset=asset,
            bbox=bbox,
            geometry=geometry,
        )
        scene.pgstac_item_id = item.id
        self._scene_repository.upsert(scene)
        if self._pgstac_repository is not None:
            self._pgstac_repository.upsert_item_json(item)
        return item.id

    def _require_dependencies(self, mode: str) -> None:
        if self._aoi_repository is None:
            raise ValueError("EOS-04 backfill requires an AOI repository")
        if self._settings.runtime_backend != RuntimeBackend.MEMORY:
            if (
                self._settings.bhoonidhi_approved_runtime_required
                and not self._settings.bhoonidhi_approved_runtime
            ):
                raise ValueError("Bhoonidhi EOS-04 live jobs require approved runtime")
            validate_resourcesat_runtime_roots(self._settings, dry_run=False)
            required = self._settings.source_mirror_required_headroom_bytes
            available = shutil.disk_usage(
                _disk_usage_path(self._settings.scratch_dir)
            ).free
            if required > 0 and available < required:
                raise ValueError("insufficient EOS-04 disk headroom")
        if self._reaches(mode, "raw_download") and self._object_store is None:
            raise ValueError("EOS-04 download requires object storage")
        if self._reaches(mode, "prepare_scene") and (
            self._scene_repository is None or self._asset_repository is None
        ):
            raise ValueError("EOS-04 preparation requires scene and asset repositories")

    def _reaches(self, mode: str, stage: str) -> bool:
        return EOS04_STAGE_NAMES.index(stage) <= EOS04_STAGE_NAMES.index(EOS04_MODE_LIMIT[mode])

    def _complete(
        self,
        job: Job,
        summary: Eos04BackfillSummary,
        started_at: datetime,
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
            job_id=self._job.job_id,
            stage_name=self._name,
            metadata=self._metadata,
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
                error_code="eos04_stage_failed",
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
    return safe[:120] or "eos04"


def _disk_usage_path(path: Path) -> Path:
    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    return current
