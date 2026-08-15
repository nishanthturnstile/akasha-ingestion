from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
from rasterio.enums import Resampling
from rasterio.warp import reproject

from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.backfill_repository import BackfillRunRecord
from akasha.catalog.pgstac_repository import build_derived_item
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.catalog.sync_ledger_repository import (
    InMemorySyncLedgerRepository,
    SyncLedgerRecord,
)
from akasha.catalog.tile_layer_repository import TileLayerRecord
from akasha.config import RuntimeBackend, Settings
from akasha.jobs.idempotency import compute_backfill_idempotency_key
from akasha.jobs.store import Job, JobStatus
from akasha.processing.cog import cog_metadata, write_cog_bytes
from akasha.processing.indices import calculate_index, encode_index_output
from akasha.processing.raster_stats import RasterBand, read_single_band
from akasha.processing.sentinel2 import (
    SENTINEL2_INDEX_ASSETS,
    SENTINEL2_REQUIRED_ASSETS,
    reflectance_from_dn,
    scl_valid_mask,
    validate_required_assets,
)
from akasha.processing.stac_assets import build_asset_manifest
from akasha.providers.contracts import (
    NormalizedAsset,
    NormalizedStacItem,
    ProviderDataError,
    ProviderErrorCategory,
    ProviderSearchRequest,
    ProviderSearchResult,
)
from akasha.providers.earthsearch import EarthSearchProvider
from akasha.schemas import SyncRequest
from akasha.services.source_mirroring import SourceMirroringService


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    searched_count: int
    accepted_count: int
    mirrored_asset_count: int
    processed_count: int
    skipped_count: int
    failed_count: int
    estimated_source_mirror_bytes: int | None = None
    actual_source_mirror_bytes: int = 0
    stac_item_ids: list[str] = field(default_factory=list)
    logical_scene_keys: list[str] = field(default_factory=list)
    mirror_checksums: dict[str, str] = field(default_factory=dict)
    failed_items: dict[str, str] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, object]:
        return {
            "searched_count": self.searched_count,
            "accepted_count": self.accepted_count,
            "mirrored_asset_count": self.mirrored_asset_count,
            "processed_count": self.processed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "estimated_source_mirror_bytes": self.estimated_source_mirror_bytes,
            "actual_source_mirror_bytes": self.actual_source_mirror_bytes,
            "stac_item_ids": self.stac_item_ids,
            "logical_scene_keys": self.logical_scene_keys,
            "mirror_checksums": self.mirror_checksums,
            "failed_items": self.failed_items,
            "profile_version": "sentinel2-l2a-earthsearch-v1",
        }


class Sentinel2IngestionService:
    def __init__(
        self,
        *,
        job_store,
        stage_store,
        backfill_repository,
        settings: Settings,
        aoi_repository=None,
        scene_repository=None,
        asset_repository=None,
        raster_repository=None,
        object_store=None,
        pgstac_repository=None,
        tile_layer_repository=None,
        provider: EarthSearchProvider | None = None,
        mirroring_service: SourceMirroringService | None = None,
        sync_ledger_repository=None,
    ) -> None:
        self._job_store = job_store
        self._stage_store = stage_store
        self._backfill_repository = backfill_repository
        self._settings = settings
        self._aoi_repository = aoi_repository
        self._scene_repository = scene_repository
        self._asset_repository = asset_repository
        self._raster_repository = raster_repository
        self._object_store = object_store
        self._pgstac_repository = pgstac_repository
        self._tile_layer_repository = tile_layer_repository
        use_empty_provider = (
            settings.runtime_backend == RuntimeBackend.MEMORY and not settings.live_provider_tests
        )
        self._provider = provider or (
            _EmptyProvider() if use_empty_provider else EarthSearchProvider(settings)
        )
        self._mirroring_service = mirroring_service
        self._sync_ledger_repository = sync_ledger_repository or InMemorySyncLedgerRepository()

    def latest_processed_acquisition_date(
        self,
        *,
        source_id: str,
        aoi_id: str,
    ) -> date | None:
        scenes = self._scene_repository.list_for_source_aoi(
            source_id=source_id,
            aoi_id=aoi_id,
        )
        for scene in reversed(scenes):
            if scene.acquisition_at is not None and self._scene_is_complete(scene):
                return scene.acquisition_at.date()
        return None

    def has_active_backfill(self, *, source_id: str, aoi_id: str) -> bool:
        return any(
            job.job_type == "sentinel2_backfill"
            and job.source_id == source_id
            and job.aoi_id == aoi_id
            and job.status in {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}
            for job in self._job_store.list()
        )

    def start_backfill(self, request: SyncRequest) -> Job:
        if request.provider_route is None:
            raise ValueError("provider_route is required for sentinel2_backfill")
        idempotency_key = compute_backfill_idempotency_key(
            source_id=request.source_id,
            provider_route=request.provider_route,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
            mode=request.mode,
            request_params_version=self._settings.request_params_version,
            processing_profile_version=self._settings.sentinel2_profile_version,
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
            return self.execute_backfill(job.job_id, mode=request.mode)

        from akasha.jobs.celery_app import celery_app

        try:
            celery_app.send_task(
                "akasha.jobs.sentinel2_tasks.backfill",
                args=[job.job_id, request.mode],
            )
        except Exception as exc:
            self._job_store.mark_failed(job, error=f"task dispatch failed: {exc}")
            raise
        return job

    def execute_backfill(self, job_id: str, *, mode: str = "metadata_only") -> Job:
        job = self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")

        search_stage = None
        try:
            self._require_pipeline_dependencies()
            self._job_store.mark_running(job)
            search_stage = self._stage_store.start_stage(job_id=job.job_id, stage_name="search")
            aoi = self._aoi_repository.get(job.aoi_id)
            if aoi is None:
                raise ValueError(f"AOI not found: {job.aoi_id}")
            start_date = datetime.fromisoformat(job.date_start).date()
            end_date = datetime.fromisoformat(job.date_end).date()
            summary = self._empty_summary()
            for provider_date in _date_range(start_date, end_date):
                day_summary, exhausted = self._execute_provider_day(
                    job=job,
                    aoi=aoi,
                    provider_date=provider_date,
                    mode=mode,
                )
                summary = _merge_summaries(summary, day_summary)
                if not exhausted:
                    # A safety-cap day has not been fully searched.  Keep the job
                    # retryable so the next run revisits the same provider date.
                    continue
            self._stage_store.mark_completed(search_stage.stage_id, metadata=summary.to_metadata())
            self._upsert_backfill_summary(job, summary)
            return self._job_store.mark_completed(
                job,
                result_metadata={
                    "backfill_summary": summary.to_metadata(),
                    "mode": mode,
                },
            )
        except Exception as exc:
            if search_stage is not None:
                self._stage_store.mark_failed(
                    search_stage.stage_id,
                    error_code="processing_failed",
                    error_message=str(exc),
                )
            self._job_store.mark_failed(job, error=str(exc))
            raise

    def _execute_provider_day(
        self,
        *,
        job,
        aoi,
        provider_date: date,
        mode: str,
    ) -> tuple[BackfillSummary, bool]:
        existing = self._sync_ledger_repository.get(
            source_id=job.source_id, aoi_id=job.aoi_id, provider_date=provider_date
        )
        ledger = SyncLedgerRecord(
            source_id=job.source_id,
            aoi_id=job.aoi_id,
            provider_date=provider_date,
            status="running",
            retry_count=(existing.retry_count if existing else 0),
            started_at=existing.started_at if existing else datetime.now(UTC),
            metadata={"mode": mode},
        )
        self._sync_ledger_repository.upsert(ledger)
        request = ProviderSearchRequest(
            source_id=job.source_id,
            provider_collection="sentinel-2-l2a",
            date_start=provider_date,
            date_end=provider_date,
            intersects=aoi.geometry,
            # Search is intentionally unfiltered.  Field-clipped quality decides
            # usability after every returned scene has been registered/processed.
            max_cloud_percentage=None,
            required_assets=SENTINEL2_REQUIRED_ASSETS,
            max_items=self._settings.backfill_search_item_cap,
        )
        try:
            result = self._provider_search(request)
        except ProviderDataError as exc:
            ledger.status = "retry" if _retryable_provider_error(exc) else "failed"
            ledger.retry_count += 1
            ledger.last_error = str(exc)
            ledger.heartbeat_at = datetime.now(UTC)
            ledger.completed_at = datetime.now(UTC)
            self._sync_ledger_repository.upsert(ledger)
            raise
        ledger.searched_count = len(result.items)
        # A late-publication overlap must never erase an acquisition that was
        # registered by an earlier exhaustive search if the provider
        # transiently returns fewer records on a later pass.
        ledger.scene_count = max(existing.scene_count if existing else 0, len(result.items))
        ledger.search_complete = result.exhausted
        ledger.heartbeat_at = datetime.now(UTC)
        # Publish discovery progress before potentially long asset mirroring and
        # raster processing so monitoring reports a real backlog and heartbeat
        # while the provider day is still running.
        self._sync_ledger_repository.upsert(ledger)
        summary = self._process_items(job=job, items=result.items, mode=mode)
        processed_count = sum(
            1
            for item in result.items
            if (
                (scene := self._scene_repository.get_by_provider_product(
                    provider_adapter=item.provider_adapter,
                    provider_product_id=item.stac_item_id,
                ))
                is not None
                and self._scene_is_complete(scene)
            )
        )
        ledger.processed_count = max(
            existing.processed_count if existing else 0,
            processed_count,
        )
        ledger.failed_count = summary.failed_count
        ledger.last_error = next(iter(summary.failed_items.values()), None)
        ledger.completed_at = datetime.now(UTC)
        ledger.status = (
            "complete"
            if result.exhausted
            and summary.failed_count == 0
            and ledger.processed_count >= ledger.scene_count
            and (mode == "full_pipeline" or not result.items)
            else "partial"
        )
        if summary.failed_count and ledger.last_error:
            ledger.retry_count += 1
        self._sync_ledger_repository.upsert(ledger)
        return summary, result.exhausted

    def _provider_search(self, request: ProviderSearchRequest) -> ProviderSearchResult:
        search_with_status = getattr(self._provider, "search_with_status", None)
        if search_with_status is not None:
            return search_with_status(request)
        return ProviderSearchResult(items=self._provider.search(request))

    @staticmethod
    def _empty_summary() -> BackfillSummary:
        return BackfillSummary(
            searched_count=0,
            accepted_count=0,
            mirrored_asset_count=0,
            processed_count=0,
            skipped_count=0,
            failed_count=0,
        )

    def recover_worker_lost(self, job_id: str) -> None:
        """Close an interrupted stage before Celery redelivers a worker-lost task."""
        job = self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        for stage in self._stage_store.list_for_job(job_id):
            if stage.status.value == "running":
                self._stage_store.mark_failed(
                    stage.stage_id,
                    error_code="worker_lost",
                    error_message="Celery redelivered the task after its worker process exited.",
                )
        self._job_store.mark_queued(job)

    def _process_items(
        self,
        *,
        job: Job,
        items: list[NormalizedStacItem],
        mode: str,
    ) -> BackfillSummary:
        accepted = 0
        mirrored = 0
        processed = 0
        skipped = 0
        failed = 0
        actual_bytes = 0
        stac_item_ids: list[str] = []
        logical_scene_keys: list[str] = []
        mirror_checksums: dict[str, str] = {}
        failed_items: dict[str, str] = {}

        for item in items:
            scene: ProviderSceneRecord | None = None
            try:
                accepted += 1
                stac_item_ids.append(item.stac_item_id)
                logical_scene_keys.append(item.logical_scene_key)
                existing_scene = self._scene_repository.get_by_provider_product(
                    provider_adapter=item.provider_adapter,
                    provider_product_id=item.stac_item_id,
                )
                if existing_scene is not None and self._scene_is_complete(existing_scene):
                    if existing_scene.processing_state != "complete":
                        existing_scene.processing_state = "complete"
                        existing_scene.last_error = None
                        self._scene_repository.upsert(existing_scene)
                    skipped += 1
                    continue
                if existing_scene is not None:
                    existing_outputs = self._complete_scene_outputs(existing_scene)
                    if existing_outputs is not None:
                        self._publish_scene(existing_scene, item, existing_outputs)
                        existing_scene.processing_state = "complete"
                        existing_scene.last_error = None
                        self._scene_repository.upsert(existing_scene)
                        skipped += 1
                        continue
                scene = self._register_scene(job, item)
                self._store_manifests(item)
                validate_required_assets(item)
                asset_records = self._register_assets(scene, item)
                if mode == "metadata_only":
                    skipped += 1
                    continue
                mirrored_records = self._mirror_assets(item, asset_records)
                mirrored += len(mirrored_records)
                actual_bytes += sum(record.size_bytes or 0 for record in mirrored_records)
                mirror_checksums.update(
                    {
                        f"{item.stac_item_id}:{record.asset_key}": record.mirror_checksum_sha256
                        or ""
                        for record in mirrored_records
                    }
                )
                if mode == "mirror_only":
                    skipped += 1
                    continue
                scene.processing_state = "processing"
                scene.last_attempt_at = datetime.now(UTC)
                self._scene_repository.upsert(scene)
                processed += self._process_scene(scene=scene, item=item, assets=mirrored_records)
                scene.processing_state = "complete"
                scene.last_error = None
                self._scene_repository.upsert(scene)
            except Exception as exc:
                failed += 1
                failed_items[item.stac_item_id] = str(exc)
                if scene is not None:
                    scene.processing_state = (
                        "retrying" if isinstance(exc, ProviderDataError) else "failed"
                    )
                    scene.retry_count += 1
                    scene.last_error = str(exc)
                    scene.last_attempt_at = datetime.now(UTC)
                    self._scene_repository.upsert(scene)

        return BackfillSummary(
            searched_count=len(items),
            accepted_count=accepted,
            mirrored_asset_count=mirrored,
            processed_count=processed,
            skipped_count=skipped,
            failed_count=failed,
            actual_source_mirror_bytes=actual_bytes,
            stac_item_ids=stac_item_ids,
            logical_scene_keys=logical_scene_keys,
            mirror_checksums=mirror_checksums,
            failed_items=failed_items,
        )

    def _scene_is_complete(self, scene: ProviderSceneRecord) -> bool:
        outputs = self._complete_scene_outputs(scene)
        if outputs is None:
            return False
        return self._pgstac_repository is None or scene.pgstac_item_id is not None

    def _complete_scene_outputs(
        self,
        scene: ProviderSceneRecord,
    ) -> list[RasterOutputRecord] | None:
        if scene.id is None:
            return None
        outputs = self._raster_repository.list_for_scene_ids([scene.id])
        by_index = {output.index_name: output for output in outputs}
        if not all(index_name in by_index for index_name in SENTINEL2_INDEX_ASSETS):
            return None
        return [by_index[index_name] for index_name in SENTINEL2_INDEX_ASSETS]

    def _register_scene(self, job: Job, item: NormalizedStacItem) -> ProviderSceneRecord:
        existing = self._scene_repository.get_by_provider_product(
            provider_adapter=item.provider_adapter,
            provider_product_id=item.stac_item_id,
        )
        scene = ProviderSceneRecord(
            id=None,
            provider_adapter=item.provider_adapter,
            source_id=item.source_id,
            provider_product_id=item.stac_item_id,
            acquisition_at=item.acquisition_at,
            scene_geometry=item.footprint,
            status="accepted",
            cloud_percent=item.cloud_percent,
            license_state="open",
            provider_metadata={
                "mgrs_tile": item.mgrs_tile,
                "provider_collection": item.provider_collection,
                "provider_route": "earthsearch:sentinel-2-l2a",
            },
            aoi_id=job.aoi_id,
            logical_scene_key=item.logical_scene_key,
            processing_state="pending",
            retry_count=existing.retry_count if existing is not None else 0,
            last_error=existing.last_error if existing is not None else None,
        )
        return self._scene_repository.upsert(scene)

    def _store_manifests(self, item: NormalizedStacItem) -> None:
        self._object_store.put_stac_item(
            provider=item.provider_adapter,
            source_id=item.source_id,
            stac_item_id=item.stac_item_id,
            item=item.raw_item,
        )
        self._object_store.put_asset_manifest(
            provider=item.provider_adapter,
            source_id=item.source_id,
            stac_item_id=item.stac_item_id,
            manifest=build_asset_manifest(item),
        )

    def _register_assets(
        self,
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
    ) -> list[SceneAssetRecord]:
        records: list[SceneAssetRecord] = []
        for asset_key in SENTINEL2_REQUIRED_ASSETS:
            asset = item.assets[asset_key]
            records.append(
                self._asset_repository.upsert(
                    SceneAssetRecord(
                        id=None,
                        scene_id=scene.id or "",
                        asset_kind="source",
                        asset_key=asset.asset_key,
                        asset_href=asset.href,
                        storage_backend=asset.storage_backend,
                        selected_access_mode=asset.selected_access_mode,
                        requester_pays=asset.selected_access_mode == "requester_pays_s3",
                        scale=asset.scale,
                        offset=asset.offset,
                        nodata_value=asset.nodata,
                        roles=asset.roles,
                        media_type=asset.media_type,
                        mirror_status="pending",
                        metadata={"alternate_hrefs": asset.alternate_hrefs},
                    )
                )
            )
        return records

    def _mirror_assets(
        self,
        item: NormalizedStacItem,
        asset_records: list[SceneAssetRecord],
    ) -> list[SceneAssetRecord]:
        service = self._mirroring_service or SourceMirroringService(
            object_store=self._object_store,
            settings=self._settings,
        )
        mirrored: list[SceneAssetRecord] = []
        for record in asset_records:
            if (
                record.mirror_status == "mirrored"
                and record.mirror_object_path is not None
                and record.mirror_checksum_sha256 is not None
            ):
                mirrored.append(record)
                continue
            asset = item.assets[record.asset_key or ""]
            result = service.mirror_asset(item=item, asset=asset)
            mirrored.append(
                self._asset_repository.update_mirror(
                    record.id or "",
                    mirror_status="mirrored",
                    mirror_object_path=result.object_path,
                    mirror_checksum_sha256=result.checksum_sha256,
                    size_bytes=result.size_bytes,
                )
            )
        return mirrored

    def _process_scene(
        self,
        *,
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
        assets: list[SceneAssetRecord],
    ) -> int:
        assets_by_key = {record.asset_key or "": record for record in assets}
        scl = self._source_band(assets_by_key["scl"])
        pending_outputs: list[RasterOutputRecord] = []
        for index_name, (first_key, second_key) in SENTINEL2_INDEX_ASSETS.items():
            first_asset = item.assets[first_key]
            second_asset = item.assets[second_key]
            first = self._reflectance_band(assets_by_key[first_key], first_asset)
            second = self._reflectance_band(assets_by_key[second_key], second_asset)
            second_values = _match_grid(second, first, resampling=Resampling.bilinear)
            scl_values = _match_grid(scl, first, resampling=Resampling.nearest).astype("uint8")
            valid_mask = (
                np.isfinite(first.values) & np.isfinite(second_values) & scl_valid_mask(scl_values)
            )
            output_transform = first.transform
            output_crs = first.crs
            values = calculate_index(
                index_name,
                first.values,
                second_values,
                valid_mask=valid_mask,
            )
            del first, second, second_values, scl_values, valid_mask
            encoded, profile = encode_index_output(index_name, values)
            del values
            payload = write_cog_bytes(
                encoded,
                transform=output_transform,
                crs=output_crs,
                nodata=profile.nodata_value,
                tags={
                    "akasha:formula_version": profile.formula_version,
                    "akasha:processing_profile_version": self._settings.sentinel2_profile_version,
                    "akasha:source_item_id": item.stac_item_id,
                },
            )
            metadata = cog_metadata(
                encoded,
                crs=output_crs,
                resolution=profile.processing_resolution,
                nodata=profile.nodata_value,
            )
            del encoded
            object_path, checksum = self._object_store.put_derived_cog(
                provider=item.provider_adapter,
                source_id=item.source_id,
                stac_item_id=item.stac_item_id,
                index_name=index_name,
                payload=payload,
                metadata={
                    "source-id": item.source_id,
                    "stac-item-id": item.stac_item_id,
                    "index-name": index_name,
                },
            )
            del payload
            pending_outputs.append(
                RasterOutputRecord(
                    id=None,
                    scene_id=scene.id or "",
                    output_kind="derived_index",
                    index_name=index_name,
                    object_path=object_path,
                    checksum_sha256=checksum,
                    formula_version=profile.formula_version,
                    processing_profile_version=self._settings.sentinel2_profile_version,
                    dtype=profile.dtype,
                    scale_factor=profile.scale_factor,
                    nodata_value=profile.nodata_value,
                    min_value=metadata["min_value"],
                    max_value=metadata["max_value"],
                    native_resolution=profile.processing_resolution,
                    processing_resolution=profile.processing_resolution,
                    display_resolution=profile.processing_resolution,
                    crs=output_crs,
                    cloud_mask_version="scl-v1",
                    metadata={
                        "pgstac_collection": "akasha-sentinel-2-l2a-derived-v1",
                        "pgstac_asset_key": index_name,
                        "pgstac_href": f"s3://{self._settings.minio_bucket}/{object_path}",
                        "mask_object_path": assets_by_key["scl"].mirror_object_path,
                    },
                )
            )

        output_records: list[RasterOutputRecord] = []
        for pending_output in pending_outputs:
            output = self._raster_repository.upsert_derived_index(pending_output)
            output_records.append(output)
            self._tile_layer_repository.upsert_for_raster(
                TileLayerRecord(
                    layer_id=None,
                    raster_output_id=output.id or "",
                    visibility="private",
                    metadata={"index_name": output.index_name, "scene_id": scene.id},
                )
            )

        self._publish_scene(scene, item, output_records)
        return len(output_records)

    def _publish_scene(
        self,
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
        outputs: list[RasterOutputRecord],
    ) -> None:
        if self._pgstac_repository is None or not scene.scene_geometry:
            return
        item_json = build_derived_item(
            scene=scene,
            outputs=outputs,
            bbox=item.bbox,
            geometry=scene.scene_geometry,
            source_assets=self._asset_repository.list_for_scene(scene.id or ""),
            mirror_bucket=self._settings.minio_bucket,
        )
        self._pgstac_repository.upsert_item_json(item_json)
        scene.pgstac_item_id = item_json.id
        self._scene_repository.upsert(scene)

    def _source_band(self, asset: SceneAssetRecord) -> RasterBand:
        if asset.mirror_object_path is None:
            raise ValueError(f"asset is not mirrored: {asset.asset_key}")
        return read_single_band(self._object_store.get_required(asset.mirror_object_path))

    def _reflectance_band(
        self,
        asset_record: SceneAssetRecord,
        asset: NormalizedAsset,
    ) -> RasterBand:
        source = self._source_band(asset_record)
        nodata = asset.nodata
        valid_mask = np.ones(source.values.shape, dtype=bool)
        if nodata is not None:
            valid_mask &= source.values != nodata
        values = reflectance_from_dn(
            source.values,
            scale=asset.scale or 1.0,
            offset=asset.offset,
            valid_mask=valid_mask,
        )
        return RasterBand(values=values, transform=source.transform, crs=source.crs, nodata=np.nan)

    def _upsert_backfill_summary(self, job: Job, summary: BackfillSummary) -> None:
        self._backfill_repository.upsert(
            BackfillRunRecord(
                id=None,
                job_id=job.job_id,
                source_id=job.source_id,
                aoi_id=job.aoi_id,
                date_start=datetime.fromisoformat(job.date_start).date(),
                date_end=datetime.fromisoformat(job.date_end).date(),
                status="completed" if summary.failed_count == 0 else "partial",
                searched_count=summary.searched_count,
                accepted_count=summary.accepted_count,
                mirrored_asset_count=summary.mirrored_asset_count,
                processed_count=summary.processed_count,
                skipped_count=summary.skipped_count,
                failed_count=summary.failed_count,
                actual_source_mirror_bytes=summary.actual_source_mirror_bytes,
                summary_json=summary.to_metadata(),
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )

    def _require_pipeline_dependencies(self) -> None:
        missing = [
            name
            for name, value in {
                "aoi_repository": self._aoi_repository,
                "scene_repository": self._scene_repository,
                "asset_repository": self._asset_repository,
                "raster_repository": self._raster_repository,
                "object_store": self._object_store,
                "tile_layer_repository": self._tile_layer_repository,
            }.items()
            if value is None
        ]
        if missing:
            raise RuntimeError(f"sentinel2 pipeline dependencies missing: {', '.join(missing)}")


def _match_grid(
    source: RasterBand,
    reference: RasterBand,
    *,
    resampling: Resampling,
) -> np.ndarray:
    if source.values.shape == reference.values.shape and source.transform == reference.transform:
        return source.values
    destination = np.empty(reference.values.shape, dtype=source.values.dtype)
    reproject(
        source.values,
        destination,
        src_transform=source.transform,
        src_crs=source.crs,
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        resampling=resampling,
        src_nodata=source.nodata,
    )
    return destination


class _EmptyProvider:
    def search(self, request: ProviderSearchRequest) -> list[NormalizedStacItem]:
        del request
        return []


def _date_range(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _retryable_provider_error(error: ProviderDataError) -> bool:
    return error.category in {
        ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
        ProviderErrorCategory.SOURCE_RATE_LIMITED,
        ProviderErrorCategory.EXTERNAL_ASSET_READ_FAILED,
        ProviderErrorCategory.DOWNLOAD_FAILED,
    }


def _merge_summaries(first: BackfillSummary, second: BackfillSummary) -> BackfillSummary:
    return BackfillSummary(
        searched_count=first.searched_count + second.searched_count,
        accepted_count=first.accepted_count + second.accepted_count,
        mirrored_asset_count=first.mirrored_asset_count + second.mirrored_asset_count,
        processed_count=first.processed_count + second.processed_count,
        skipped_count=first.skipped_count + second.skipped_count,
        failed_count=first.failed_count + second.failed_count,
        actual_source_mirror_bytes=first.actual_source_mirror_bytes
        + second.actual_source_mirror_bytes,
        stac_item_ids=first.stac_item_ids + second.stac_item_ids,
        logical_scene_keys=first.logical_scene_keys + second.logical_scene_keys,
        mirror_checksums={**first.mirror_checksums, **second.mirror_checksums},
        failed_items={**first.failed_items, **second.failed_items},
    )
