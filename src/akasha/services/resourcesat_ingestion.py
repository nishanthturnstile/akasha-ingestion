from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from akasha.catalog.aoi_repository import AoiRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import RuntimeBackend, Settings, validate_resourcesat_runtime_roots
from akasha.jobs.idempotency import (
    compute_resourcesat_backfill_idempotency_key,
    compute_resourcesat_composite_idempotency_key,
    compute_resourcesat_download_idempotency_key,
    compute_resourcesat_index_output_idempotency_key,
    compute_resourcesat_prepare_idempotency_key,
)
from akasha.jobs.stage_store import JobStage
from akasha.jobs.store import Job, JobStatus
from akasha.processing.resourcesat import ResourceSatProfile, profile_for_source
from akasha.processing.resourcesat_composite import (
    ResourceSatCompositeBuildResult,
    build_resource_sat_composite,
    verify_resource_sat_composite,
)
from akasha.processing.resourcesat_prepare import (
    PreparedResourceSatScene,
    SelectedResourceSatProduct,
    prepare_resourcesat_product,
)
from akasha.providers.bhoonidhi import BhoonidhiCandidate, BhoonidhiClient, redact_string
from akasha.providers.contracts import ProviderDataError, ProviderErrorCategory
from akasha.schemas import SyncRequest
from akasha.services.resourcesat_outputs import (
    BHOONIDHI_PROVIDER,
    ResourceSatDerivedOutputResult,
    generate_resourcesat_derived_indices,
    provider_scene_from_composite_manifest,
    provider_scene_from_prepare_manifest,
    scene_asset_records_from_composite_manifest,
    scene_asset_records_from_prepare_manifest,
)
from akasha.storage.object_store import file_sha256

BackfillTaskDispatcher = Callable[[str, str, str], None]
PrepareProductCallable = Callable[..., PreparedResourceSatScene]
BuildCompositeCallable = Callable[..., ResourceSatCompositeBuildResult]
VerifyCompositeCallable = Callable[..., Any]
GenerateDerivedCallable = Callable[..., ResourceSatDerivedOutputResult]

RESOURCESAT_BACKFILL_TASK = "akasha.jobs.resourcesat_tasks.backfill"

RESOURCESAT_STAGE_NAMES = (
    "provider_search",
    "raw_download",
    "prepare_scene",
    "scene_validation",
    "composite",
    "composite_validation",
    "index_generation",
    "pgstac_registration",
    "readiness_refresh",
    "cleanup",
)

MODE_LIMIT_STAGE = {
    "metadata_only": "provider_search",
    "download_only": "raw_download",
    "prepare_only": "scene_validation",
    "composite_only": "composite_validation",
    "full_pipeline": "cleanup",
}


@dataclass(slots=True)
class ResourceSatBackfillSummary:
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
    composite_count: int = 0
    index_output_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)
    product_ids: list[str] = field(default_factory=list)
    output_ids: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_metadata(self) -> dict[str, object]:
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
            "composite_count": self.composite_count,
            "index_output_count": self.index_output_count,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "stage_counts": dict(self.stage_counts),
            "product_ids": list(self.product_ids),
            "output_ids": list(self.output_ids),
            "failures": list(self.failures),
        }


@dataclass(frozen=True, slots=True)
class _RawDownload:
    candidate: BhoonidhiCandidate
    raw_path: Path
    raw_object_path: str
    raw_checksum_sha256: str


class ResourceSatIngestionService:
    def __init__(
        self,
        *,
        job_store: Any,
        stage_store: Any,
        settings: Settings,
        aoi_repository: Any,
        object_store: Any,
        bhoonidhi_client: BhoonidhiClient,
        source_provider_route_repository: Any | None = None,
        scene_repository: Any | None = None,
        asset_repository: Any | None = None,
        raster_repository: Any | None = None,
        tile_layer_repository: Any | None = None,
        pgstac_repository: Any | None = None,
        task_dispatcher: BackfillTaskDispatcher | None = None,
        prepare_product: PrepareProductCallable = prepare_resourcesat_product,
        build_composite: BuildCompositeCallable = build_resource_sat_composite,
        verify_composite: VerifyCompositeCallable = verify_resource_sat_composite,
        generate_derived: GenerateDerivedCallable = generate_resourcesat_derived_indices,
    ) -> None:
        self._job_store = job_store
        self._stage_store = stage_store
        self._settings = settings
        self._aoi_repository = aoi_repository
        self._object_store = object_store
        self._bhoonidhi_client = bhoonidhi_client
        self._source_provider_route_repository = source_provider_route_repository
        self._scene_repository = scene_repository
        self._asset_repository = asset_repository
        self._raster_repository = raster_repository
        self._tile_layer_repository = tile_layer_repository
        self._pgstac_repository = pgstac_repository
        self._task_dispatcher = task_dispatcher
        self._prepare_product = prepare_product
        self._build_composite = build_composite
        self._verify_composite = verify_composite
        self._generate_derived = generate_derived

    def start_backfill(self, request: SyncRequest) -> Job:
        if request.job_type != "resourcesat_backfill":
            raise ValueError("ResourceSatIngestionService only handles resourcesat_backfill")
        profile = profile_for_source(request.source_id)
        provider_route = request.provider_route or f"{BHOONIDHI_PROVIDER}:{profile.collection_id}"
        idempotency_key = compute_resourcesat_backfill_idempotency_key(
            source_id=request.source_id,
            provider_route=provider_route,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
            mode=request.mode,
            request_params_version=self._settings.request_params_version,
            processing_profile_version=profile.processing_profile_version,
        )
        job, created = self._job_store.create_or_get(
            job_type=request.job_type,
            idempotency_key=idempotency_key,
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
                RESOURCESAT_BACKFILL_TASK,
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
        provider_route: str | None = None,
    ) -> Job:
        if mode not in MODE_LIMIT_STAGE:
            raise ValueError(f"unsupported ResourceSat backfill mode: {mode}")
        job = self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        if job.status == JobStatus.COMPLETED:
            return job

        started_at = datetime.now(UTC)
        self._job_store.mark_running(job)
        try:
            self._require_pipeline_dependencies(mode)
            profile = profile_for_source(job.source_id)
            resolved_provider_route = provider_route or (
                f"{BHOONIDHI_PROVIDER}:{profile.collection_id}"
            )
            start = date.fromisoformat(job.date_start)
            end = date.fromisoformat(job.date_end)
            summary = ResourceSatBackfillSummary(
                source_id=job.source_id,
                provider_route=resolved_provider_route,
                aoi_id=job.aoi_id,
                date_start=start,
                date_end=end,
                mode=mode,
            )
            aoi = self._aoi(job.aoi_id)
            candidates = self._run_search_stage(job, profile, aoi, summary)
            if not self._mode_reaches(mode, "raw_download"):
                self._mark_remaining_skipped(job, mode, summary)
                return self._complete_job(job, summary, started_at)

            downloads = self._run_download_stage(
                job,
                profile,
                resolved_provider_route,
                candidates,
                summary,
            )
            if not self._mode_reaches(mode, "prepare_scene"):
                self._mark_remaining_skipped(job, mode, summary)
                return self._complete_job(job, summary, started_at)

            prepared = self._run_prepare_stage(
                job,
                profile,
                resolved_provider_route,
                downloads,
                summary,
            )
            manifest_paths = [
                self._persist_manifest(scene.manifest, scene.product_id)
                for scene in prepared
            ]
            self._run_scene_validation_stage(job, prepared, summary)
            if not self._mode_reaches(mode, "composite"):
                self._mark_remaining_skipped(job, mode, summary)
                return self._complete_job(job, summary, started_at)

            composite, composite_scene = self._run_composite_stage(
                job,
                profile,
                resolved_provider_route,
                aoi,
                manifest_paths,
                summary,
            )
            self._run_composite_validation_stage(job, composite, summary)
            if not self._mode_reaches(mode, "index_generation"):
                self._mark_remaining_skipped(job, mode, summary)
                return self._complete_job(job, summary, started_at)

            output_ids = self._run_index_generation_stage(
                job,
                profile,
                resolved_provider_route,
                composite,
                composite_scene,
                summary,
            )
            self._run_pgstac_registration_stage(job, output_ids, summary)
            self._run_readiness_refresh_stage(job, summary)
            self._run_cleanup_stage(job, summary)
            return self._complete_job(job, summary, started_at)
        except Exception as exc:
            self._job_store.mark_failed(job, error=redact_string(str(exc)))
            raise

    def _run_search_stage(
        self,
        job: Job,
        profile: ResourceSatProfile,
        aoi: AoiRecord,
        summary: ResourceSatBackfillSummary,
    ) -> list[BhoonidhiCandidate]:
        with self._stage(job, "provider_search", {"collection": profile.collection_id}) as stage:
            candidates = self._bhoonidhi_client.search(
                source_id=job.source_id,
                collection=profile.collection_id,
                intersects=aoi.geometry,
                aoi_bbox=aoi.bbox,
                date_start=summary.date_start,
                date_end=summary.date_end,
                max_items=self._settings.backfill_search_item_cap,
            )
            selected = [
                candidate
                for candidate in candidates
                if candidate.online and candidate.intersects_aoi
            ][: self._settings.bhoonidhi_max_downloads_per_run]
            summary.searched_count = len(candidates)
            summary.selected_count = len(selected)
            summary.product_ids = [candidate.provider_product_id for candidate in selected]
            summary.skipped_count += len(candidates) - len(selected)
            summary.stage_counts["provider_search"] = len(selected)
            stage.metadata.update(
                {
                    "searched_count": len(candidates),
                    "selected_count": len(selected),
                    "product_ids": summary.product_ids,
                }
            )
            return selected

    def _run_download_stage(
        self,
        job: Job,
        profile: ResourceSatProfile,
        provider_route: str,
        candidates: Sequence[BhoonidhiCandidate],
        summary: ResourceSatBackfillSummary,
    ) -> list[_RawDownload]:
        downloads: list[_RawDownload] = []
        with self._stage(job, "raw_download", {"candidate_count": len(candidates)}) as stage:
            for candidate in candidates:
                try:
                    download_key = compute_resourcesat_download_idempotency_key(
                        source_id=job.source_id,
                        provider_route=provider_route,
                        product_id=candidate.provider_product_id,
                        request_params_version=self._settings.request_params_version,
                        processing_profile_version=profile.processing_profile_version,
                    )
                    raw_path = self._raw_download_path(job, candidate)
                    result = self._bhoonidhi_client.download_product(
                        product_id=candidate.provider_product_id,
                        collection=candidate.collection,
                        destination=raw_path,
                    )
                    downloaded_path = Path(str(result["path"]))
                    raw_checksum = str(result.get("sha256") or file_sha256(downloaded_path))
                    raw_object_path, _ = self._object_store.put_raw_file(
                        provider=BHOONIDHI_PROVIDER,
                        source_id=job.source_id,
                        product_id=candidate.provider_product_id,
                        file_path=downloaded_path,
                        checksum_sha256=raw_checksum,
                        metadata={
                            "provider-route": provider_route,
                            "idempotency-key": download_key,
                        },
                    )
                    downloads.append(
                        _RawDownload(
                            candidate=candidate,
                            raw_path=downloaded_path,
                            raw_object_path=raw_object_path,
                            raw_checksum_sha256=raw_checksum,
                        )
                    )
                except Exception as exc:
                    self._record_failure(
                        summary,
                        "raw_download",
                        candidate.provider_product_id,
                        exc,
                    )
            summary.downloaded_count = len(downloads)
            summary.stage_counts["raw_download"] = len(downloads)
            stage.metadata.update(
                {
                    "downloaded_count": len(downloads),
                    "failed_count": len(candidates) - len(downloads),
                }
            )
            if len(downloads) != len(candidates):
                raise ProviderDataError(
                    ProviderErrorCategory.DOWNLOAD_FAILED,
                    "One or more ResourceSat raw downloads failed",
                )
            return downloads

    def _run_prepare_stage(
        self,
        job: Job,
        profile: ResourceSatProfile,
        provider_route: str,
        downloads: Sequence[_RawDownload],
        summary: ResourceSatBackfillSummary,
    ) -> list[PreparedResourceSatScene]:
        prepared: list[PreparedResourceSatScene] = []
        with self._stage(job, "prepare_scene", {"downloaded_count": len(downloads)}) as stage:
            for download in downloads:
                candidate = download.candidate
                try:
                    prepare_key = compute_resourcesat_prepare_idempotency_key(
                        source_id=job.source_id,
                        provider_route=provider_route,
                        product_id=candidate.provider_product_id,
                        raw_checksum_sha256=download.raw_checksum_sha256,
                        request_params_version=self._settings.request_params_version,
                        processing_profile_version=profile.processing_profile_version,
                    )
                    product = self._selected_product(job, candidate, download.raw_path)
                    scene = self._prepare_product(product, settings=self._settings)
                    self._attach_prepared_object_paths(scene, prepare_key)
                    persisted_scene = self._register_prepared_scene(
                        scene,
                        raw_object_path=download.raw_object_path,
                        raw_checksum_sha256=download.raw_checksum_sha256,
                        raw_size_bytes=download.raw_path.stat().st_size,
                        provider_route_id=self._provider_route_id(
                            job.source_id,
                            provider_route,
                        ),
                    )
                    scene.manifest["scene_record_id"] = persisted_scene.id
                    prepared.append(scene)
                except Exception as exc:
                    self._record_failure(
                        summary,
                        "prepare_scene",
                        candidate.provider_product_id,
                        exc,
                    )
            summary.prepared_count = len(prepared)
            summary.stage_counts["prepare_scene"] = len(prepared)
            stage.metadata.update(
                {
                    "prepared_count": len(prepared),
                    "failed_count": len(downloads) - len(prepared),
                }
            )
            if len(prepared) != len(downloads):
                raise ProviderDataError(
                    ProviderErrorCategory.PREPARE_FAILED,
                    "One or more ResourceSat scenes failed preparation",
                )
            return prepared

    def _run_scene_validation_stage(
        self,
        job: Job,
        scenes: Sequence[PreparedResourceSatScene],
        summary: ResourceSatBackfillSummary,
    ) -> None:
        with self._stage(job, "scene_validation", {"prepared_count": len(scenes)}) as stage:
            stage.metadata["validated_count"] = len(scenes)
            summary.stage_counts["scene_validation"] = len(scenes)

    def _run_composite_stage(
        self,
        job: Job,
        profile: ResourceSatProfile,
        provider_route: str,
        aoi: AoiRecord,
        manifest_paths: Sequence[Path],
        summary: ResourceSatBackfillSummary,
    ) -> tuple[ResourceSatCompositeBuildResult, ProviderSceneRecord]:
        if not manifest_paths:
            raise ProviderDataError(
                ProviderErrorCategory.PREPARE_FAILED,
                "No prepared ResourceSat scenes are available for compositing",
            )
        composite_key = compute_resourcesat_composite_idempotency_key(
            source_id=job.source_id,
            aoi_id=job.aoi_id,
            composite_date=job.date_end,
            product_ids=_manifest_product_ids(manifest_paths),
            request_params_version=self._settings.request_params_version,
            processing_profile_version=profile.processing_profile_version,
        )
        with self._stage(
            job,
            "composite",
            {"prepared_count": len(manifest_paths), "idempotency_key": composite_key},
        ) as stage:
            composite = self._build_composite(
                manifest_paths=list(manifest_paths),
                aoi=aoi,
                output_root=self._composite_root(job),
                settings=self._settings,
            )
            self._attach_composite_object_paths(composite, composite_key)
            composite_scene = self._register_composite_scene(
                composite,
                provider_route_id=self._provider_route_id(job.source_id, provider_route),
            )
            composite.manifest["scene_record_id"] = composite_scene.id
            self._persist_manifest_at(composite.manifest_path, composite.manifest)
            summary.composite_count = 1
            summary.stage_counts["composite"] = 1
            stage.metadata.update(
                {
                    "composite_count": 1,
                    "scene_record_id": composite_scene.id,
                    "manifest_path": str(composite.manifest_path),
                }
            )
            return composite, composite_scene

    def _run_composite_validation_stage(
        self,
        job: Job,
        composite: ResourceSatCompositeBuildResult,
        summary: ResourceSatBackfillSummary,
    ) -> None:
        with self._stage(
            job,
            "composite_validation",
            {"manifest_path": str(composite.manifest_path)},
        ) as stage:
            verification = self._verify_composite(composite.manifest_path, settings=self._settings)
            stage.metadata["verification"] = _jsonable(verification)
            summary.stage_counts["composite_validation"] = 1

    def _run_index_generation_stage(
        self,
        job: Job,
        profile: ResourceSatProfile,
        provider_route: str,
        composite: ResourceSatCompositeBuildResult,
        composite_scene: ProviderSceneRecord,
        summary: ResourceSatBackfillSummary,
    ) -> list[str]:
        index_key = compute_resourcesat_index_output_idempotency_key(
            source_id=job.source_id,
            provider_route=provider_route,
            scene_or_composite_id=composite_scene.id or composite_scene.provider_product_id,
            index_name="all-supported",
            formula_version=f"resourcesat-{profile.processing_profile_version}",
            request_params_version=self._settings.request_params_version,
            processing_profile_version=profile.processing_profile_version,
        )
        with self._stage(job, "index_generation", {"idempotency_key": index_key}) as stage:
            result = self._generate_derived(
                manifest_path=composite.manifest_path,
                scene=composite_scene,
                output_root=self._derived_root(job),
                settings=self._settings,
                object_store=self._object_store,
                raster_repository=self._raster_repository,
                tile_layer_repository=self._tile_layer_repository,
                pgstac_repository=self._pgstac_repository,
                scene_repository=self._scene_repository,
            )
            output_ids = [
                record.id or f"{record.scene_id}:{record.index_name}"
                for record in result.outputs
            ]
            summary.index_output_count = len(result.outputs)
            summary.processed_count = 1 if result.outputs else 0
            summary.output_ids = output_ids
            summary.stage_counts["index_generation"] = len(result.outputs)
            stage.metadata.update(
                {"index_output_count": len(result.outputs), "output_ids": output_ids}
            )
            return output_ids

    def _run_pgstac_registration_stage(
        self,
        job: Job,
        output_ids: Sequence[str],
        summary: ResourceSatBackfillSummary,
    ) -> None:
        with self._stage(job, "pgstac_registration", {"output_count": len(output_ids)}) as stage:
            stage.metadata["registered_output_ids"] = list(output_ids)
            summary.stage_counts["pgstac_registration"] = len(output_ids)

    def _run_readiness_refresh_stage(self, job: Job, summary: ResourceSatBackfillSummary) -> None:
        with self._stage(job, "readiness_refresh", {"source_id": summary.source_id}) as stage:
            stage.metadata.update(
                {
                    "processed_count": summary.processed_count,
                    "failed_count": summary.failed_count,
                    "phase9_readiness_refactor_required": True,
                }
            )
            summary.stage_counts["readiness_refresh"] = 1

    def _run_cleanup_stage(self, job: Job, summary: ResourceSatBackfillSummary) -> None:
        with self._stage(job, "cleanup", {"source_id": summary.source_id}) as stage:
            stage.metadata["retained_for_audit"] = True
            summary.stage_counts["cleanup"] = 1

    def _register_prepared_scene(
        self,
        scene: PreparedResourceSatScene,
        *,
        raw_object_path: str,
        raw_checksum_sha256: str,
        raw_size_bytes: int,
        provider_route_id: str | None,
    ) -> ProviderSceneRecord:
        scene_record = provider_scene_from_prepare_manifest(
            scene.manifest,
            raw_object_path=raw_object_path,
            provider_route_id=provider_route_id,
        )
        if self._scene_repository is None:
            scene_record.id = scene_record.provider_product_id
            return scene_record
        persisted_scene = self._scene_repository.upsert(scene_record)
        if self._asset_repository is not None:
            for asset in scene_asset_records_from_prepare_manifest(
                persisted_scene,
                scene.manifest,
                raw_object_path=raw_object_path,
                raw_checksum_sha256=raw_checksum_sha256,
                raw_size_bytes=raw_size_bytes,
            ):
                self._asset_repository.upsert(asset)
        return persisted_scene

    def _register_composite_scene(
        self,
        composite: ResourceSatCompositeBuildResult,
        *,
        provider_route_id: str | None,
    ) -> ProviderSceneRecord:
        scene_record = provider_scene_from_composite_manifest(
            composite.manifest,
            provider_route_id=provider_route_id,
        )
        if self._scene_repository is None:
            scene_record.id = scene_record.provider_product_id
            return scene_record
        persisted_scene = self._scene_repository.upsert(scene_record)
        if self._asset_repository is not None:
            for asset in scene_asset_records_from_composite_manifest(
                persisted_scene,
                composite.manifest,
            ):
                self._asset_repository.upsert(asset)
        return persisted_scene

    def _attach_prepared_object_paths(
        self,
        scene: PreparedResourceSatScene,
        idempotency_key: str,
    ) -> None:
        for asset_key, output in _manifest_outputs(scene.manifest).items():
            output_path = output.get("path")
            if not output_path:
                continue
            object_path, checksum = self._object_store.put_prepared_cog_file(
                provider=BHOONIDHI_PROVIDER,
                source_id=scene.source_id,
                product_id=scene.product_id,
                asset_key=asset_key,
                file_path=Path(str(output_path)),
                checksum_sha256=str(output.get("checksum_sha256") or ""),
                metadata={"idempotency-key": idempotency_key},
            )
            output["object_path"] = object_path
            output["checksum_sha256"] = checksum

    def _attach_composite_object_paths(
        self,
        composite: ResourceSatCompositeBuildResult,
        idempotency_key: str,
    ) -> None:
        for asset_key, output in _manifest_outputs(composite.manifest).items():
            output_path = output.get("path")
            if not output_path:
                continue
            object_path, checksum = self._object_store.put_composite_cog_file(
                source_id=str(composite.manifest["source_id"]),
                aoi_id=str(composite.manifest["aoi_id"]),
                composite_date=str(composite.manifest["composite_date"]),
                asset_key=asset_key,
                file_path=Path(str(output_path)),
                checksum_sha256=str(output.get("checksum_sha256") or ""),
                metadata={"idempotency-key": idempotency_key},
            )
            output["object_path"] = object_path
            output["checksum_sha256"] = checksum
        self._object_store.put_composite_manifest(
            source_id=str(composite.manifest["source_id"]),
            aoi_id=str(composite.manifest["aoi_id"]),
            composite_date=str(composite.manifest["composite_date"]),
            manifest=composite.manifest,
        )

    def _selected_product(
        self,
        job: Job,
        candidate: BhoonidhiCandidate,
        raw_path: Path,
    ) -> SelectedResourceSatProduct:
        return SelectedResourceSatProduct(
            source_id=job.source_id,
            product_id=candidate.provider_product_id,
            package_path=raw_path,
            acquisition_at=candidate.acquisition_at,
            aoi_id=job.aoi_id,
            bbox=candidate.bbox,
            geometry=_candidate_geometry(candidate),
            provider_metadata={
                "collection": candidate.collection,
                "item_id": candidate.item_id,
                "provider_properties": candidate.provider_properties,
                "provider_metadata": candidate.provider_metadata,
            },
        )

    def _provider_route_id(self, source_id: str, provider_route: str) -> str | None:
        if self._source_provider_route_repository is None:
            return None
        route = self._source_provider_route_repository.get_by_route_key(source_id, provider_route)
        return route.id if route is not None else None

    def _mark_remaining_skipped(
        self,
        job: Job,
        mode: str,
        summary: ResourceSatBackfillSummary,
    ) -> None:
        limit_stage = MODE_LIMIT_STAGE[mode]
        should_skip = False
        for stage_name in RESOURCESAT_STAGE_NAMES:
            if should_skip:
                with self._stage(job, stage_name, {"skipped": True, "mode": mode}):
                    summary.stage_counts[stage_name] = 0
            if stage_name == limit_stage:
                should_skip = True

    def _mode_reaches(self, mode: str, stage_name: str) -> bool:
        return RESOURCESAT_STAGE_NAMES.index(stage_name) <= RESOURCESAT_STAGE_NAMES.index(
            MODE_LIMIT_STAGE[mode]
        )

    def _aoi(self, aoi_id: str) -> AoiRecord:
        aoi = self._aoi_repository.get(aoi_id)
        if aoi is None:
            raise ValueError(f"AOI not found: {aoi_id}")
        return aoi

    def _require_pipeline_dependencies(self, mode: str) -> None:
        if self._aoi_repository is None:
            raise ValueError("ResourceSat backfill requires an AOI repository")
        self._require_approved_runtime()
        if self._object_store is None and self._mode_reaches(mode, "raw_download"):
            raise ValueError("ResourceSat download stages require an object store")
        if self._mode_reaches(mode, "raw_download"):
            validate_resourcesat_runtime_roots(
                self._settings,
                dry_run=self._settings.runtime_backend == RuntimeBackend.MEMORY,
            )
            self._require_disk_headroom()
        if self._mode_reaches(mode, "prepare_scene") and (
            self._scene_repository is None or self._asset_repository is None
        ):
            raise ValueError("ResourceSat prepare stages require scene and asset repositories")
        if self._mode_reaches(mode, "index_generation") and self._raster_repository is None:
            raise ValueError("ResourceSat index generation requires a raster repository")

    def _require_approved_runtime(self) -> None:
        if self._settings.runtime_backend == RuntimeBackend.MEMORY:
            return
        if not self._settings.bhoonidhi_approved_runtime_required:
            return
        if self._settings.bhoonidhi_approved_runtime:
            return
        raise ValueError("Bhoonidhi ResourceSat live jobs require approved runtime")

    def _require_disk_headroom(self) -> None:
        required = self._settings.source_mirror_required_headroom_bytes
        if self._settings.runtime_backend == RuntimeBackend.MEMORY or required <= 0:
            return
        free = shutil.disk_usage(_disk_usage_path(self._settings.scratch_dir)).free
        if free < required:
            raise ValueError(
                f"insufficient ResourceSat disk headroom: required {required} bytes, "
                f"available {free} bytes"
            )

    def _record_failure(
        self,
        summary: ResourceSatBackfillSummary,
        stage: str,
        product_id: str,
        exc: Exception,
    ) -> None:
        summary.failed_count += 1
        summary.failures.append(
            {
                "stage": stage,
                "product_id": product_id,
                "error": redact_string(str(exc)),
            }
        )

    def _complete_job(
        self,
        job: Job,
        summary: ResourceSatBackfillSummary,
        started_at: datetime,
    ) -> Job:
        metadata = summary.to_metadata()
        metadata["duration_seconds"] = (datetime.now(UTC) - started_at).total_seconds()
        return self._job_store.mark_completed(
            job,
            result_metadata={
                "backfill_summary": metadata,
                "mode": summary.mode,
                "provider_route": summary.provider_route,
            },
        )

    def _raw_download_path(self, job: Job, candidate: BhoonidhiCandidate) -> Path:
        return (
            Path(self._settings.scratch_dir)
            / "resourcesat-downloads"
            / job.job_id
            / _safe_component(candidate.provider_product_id)
            / "original.zip"
        )

    def _composite_root(self, job: Job) -> Path:
        return Path(self._settings.scratch_dir) / "resourcesat-composite" / job.job_id

    def _derived_root(self, job: Job) -> Path:
        return Path(self._settings.scratch_dir) / "resourcesat-derived" / job.job_id

    def _persist_manifest(self, manifest: dict[str, Any], product_id: str) -> Path:
        manifest_dir = (
            Path(self._settings.scratch_dir)
            / "resourcesat-manifests"
            / _safe_component(product_id)
        )
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / "prepare_manifest.json"
        self._persist_manifest_at(path, manifest)
        return path

    @staticmethod
    def _persist_manifest_at(path: Path, manifest: dict[str, Any]) -> None:
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    def _stage(self, job: Job, stage_name: str, metadata: dict[str, Any]) -> _StageContext:
        return _StageContext(self._stage_store, job, stage_name, metadata)


class _StageContext:
    def __init__(
        self,
        stage_store: Any,
        job: Job,
        stage_name: str,
        metadata: dict[str, Any],
    ) -> None:
        self._stage_store = stage_store
        self._job = job
        self._stage_name = stage_name
        self._metadata = metadata
        self.stage: JobStage | None = None

    def __enter__(self) -> JobStage:
        self.stage = self._stage_store.start_stage(
            job_id=self._job.job_id,
            stage_name=self._stage_name,
            metadata=self._metadata,
        )
        return self.stage

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        if self.stage is None:
            return
        if exc is None:
            self._stage_store.mark_completed(self.stage.stage_id, metadata=self.stage.metadata)
        else:
            self._stage_store.mark_failed(
                self.stage.stage_id,
                error_code="resourcesat_stage_failed",
                error_message=str(exc),
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
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def _manifest_outputs(manifest: dict[str, Any]) -> dict[str, Any]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("ResourceSat manifest outputs missing or invalid")
    return outputs


def _manifest_product_ids(manifest_paths: Sequence[Path]) -> list[str]:
    product_ids: list[str] = []
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        product_id = manifest.get("product_id")
        if product_id:
            product_ids.append(str(product_id))
    return product_ids


def _safe_component(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe[:120] or "resourcesat"


def _disk_usage_path(path: Path) -> Path:
    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Iterable):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(value.__dict__)
    return str(value)
