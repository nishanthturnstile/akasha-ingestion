from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
from rasterio.enums import Resampling
from rasterio.warp import reproject

from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.backfill_repository import BackfillRunRecord
from akasha.catalog.pgstac_repository import build_landsat_derived_item
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.catalog.tile_layer_repository import TileLayerRecord
from akasha.config import RuntimeBackend, Settings
from akasha.jobs.idempotency import compute_backfill_idempotency_key
from akasha.jobs.store import Job, JobStatus
from akasha.processing.cog import cog_metadata, write_cog_bytes
from akasha.processing.indices import calculate_index, encode_index_output
from akasha.processing.landsat import (
    LANDSAT_INDEX_ASSETS,
    LANDSAT_MASK_PROFILE_VERSION,
    LANDSAT_NATIVE_RESOLUTION_METERS,
    LANDSAT_PGSTAC_COLLECTION_ID,
    LANDSAT_PRIMARY_PROVIDER_ROUTE,
    LANDSAT_PROVIDER_COLLECTION,
    LANDSAT_REFLECTANCE_ASSETS,
    LANDSAT_REQUIRED_ASSETS,
    LANDSAT_SOURCE_ID,
    decode_qa_mask,
    index_valid_mask,
    output_profile,
    reflectance_from_dn,
    validate_item,
)
from akasha.processing.raster_stats import RasterBand, read_single_band
from akasha.processing.stac_assets import build_asset_manifest
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem, ProviderSearchRequest
from akasha.providers.planetary_computer import PlanetaryComputerLandsatProvider
from akasha.schemas import SyncRequest
from akasha.services.source_mirroring import SourceMirroringService

_ANALYTIC_NODATA = -9999.0


@dataclass(frozen=True, slots=True)
class LandsatBackfillSummary:
    searched_count: int
    accepted_count: int
    mirrored_asset_count: int
    processed_count: int
    skipped_count: int
    failed_count: int
    actual_source_mirror_bytes: int = 0
    stac_item_ids: list[str] = field(default_factory=list)
    failed_items: dict[str, str] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, object]:
        return {
            "searched_count": self.searched_count,
            "accepted_count": self.accepted_count,
            "mirrored_asset_count": self.mirrored_asset_count,
            "processed_count": self.processed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "actual_source_mirror_bytes": self.actual_source_mirror_bytes,
            "stac_item_ids": self.stac_item_ids,
            "failed_items": self.failed_items,
            "processing_profile_version": "landsat-8-9-c2-l2-sr-qa-v1",
            "mask_profile_version": LANDSAT_MASK_PROFILE_VERSION,
        }


class LandsatIngestionService:
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
        provider: PlanetaryComputerLandsatProvider | None = None,
        mirroring_service: SourceMirroringService | None = None,
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
            _EmptyProvider()
            if use_empty_provider
            else PlanetaryComputerLandsatProvider(settings)
        )
        self._mirroring_service = mirroring_service

    def latest_processed_acquisition_date(self, *, source_id: str, aoi_id: str) -> date | None:
        for scene in reversed(
            self._scene_repository.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        ):
            if scene.acquisition_at is not None and self._scene_is_complete(scene):
                return scene.acquisition_at.date()
        return None

    def has_active_backfill(self, *, source_id: str, aoi_id: str) -> bool:
        return any(
            job.job_type == "landsat_backfill"
            and job.source_id == source_id
            and job.aoi_id == aoi_id
            and job.status in {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}
            for job in self._job_store.list()
        )

    def start_backfill(self, request: SyncRequest) -> Job:
        if request.provider_route != LANDSAT_PRIMARY_PROVIDER_ROUTE:
            raise ValueError(f"provider_route must be {LANDSAT_PRIMARY_PROVIDER_ROUTE}")
        idempotency_key = compute_backfill_idempotency_key(
            source_id=request.source_id,
            provider_route=request.provider_route,
            aoi_id=request.aoi_id,
            date_start=request.date_start.isoformat(),
            date_end=request.date_end.isoformat(),
            mode=request.mode,
            request_params_version=(
                f"{self._settings.request_params_version}:{self._settings.landsat_mask_profile_version}"
            ),
            processing_profile_version=self._settings.landsat_profile_version,
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
                "akasha.jobs.landsat_tasks.backfill",
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
        stage = None
        try:
            self._require_dependencies()
            self._job_store.mark_running(job)
            stage = self._stage_store.start_stage(job_id=job.job_id, stage_name="search")
            aoi = self._aoi_repository.get(job.aoi_id)
            if aoi is None:
                raise ValueError(f"AOI not found: {job.aoi_id}")
            items = self._provider.search(
                ProviderSearchRequest(
                    source_id=job.source_id,
                    provider_collection=LANDSAT_PROVIDER_COLLECTION,
                    date_start=datetime.fromisoformat(job.date_start).date(),
                    date_end=datetime.fromisoformat(job.date_end).date(),
                    intersects=aoi.geometry,
                    max_cloud_percentage=self._settings.landsat_search_cloud_hint_percentage,
                    required_assets=LANDSAT_REQUIRED_ASSETS,
                    max_items=self._settings.backfill_search_item_cap,
                )
            )
            items.sort(key=_item_selection_key)
            summary = self._process_items(job=job, items=items, mode=mode)
            self._stage_store.mark_completed(stage.stage_id, metadata=summary.to_metadata())
            self._upsert_summary(job, summary)
            return self._job_store.mark_completed(
                job,
                result_metadata={"backfill_summary": summary.to_metadata(), "mode": mode},
            )
        except Exception as exc:
            if stage is not None:
                self._stage_store.mark_failed(
                    stage.stage_id,
                    error_code="processing_failed",
                    error_message=str(exc),
                )
            self._job_store.mark_failed(job, error=str(exc))
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
                    error_message="Celery redelivered the Landsat task after worker exit.",
                )
        self._job_store.mark_queued(job)

    def _process_items(
        self,
        *,
        job: Job,
        items: list[NormalizedStacItem],
        mode: str,
    ) -> LandsatBackfillSummary:
        accepted = mirrored = processed = skipped = failed = actual_bytes = 0
        accepted_ids: list[str] = []
        failed_items: dict[str, str] = {}
        new_scene_count = 0
        for item in items:
            try:
                identity = validate_item(item)
                accepted += 1
                accepted_ids.append(item.stac_item_id)
                existing = self._scene_repository.get_by_provider_product(
                    provider_adapter=item.provider_adapter,
                    provider_product_id=item.stac_item_id,
                )
                if existing is not None and self._scene_is_complete(existing):
                    skipped += 1
                    continue
                if mode != "metadata_only" and existing is None:
                    if new_scene_count >= self._settings.landsat_max_new_scenes_per_run:
                        skipped += 1
                        continue
                    new_scene_count += 1
                scene = self._register_scene(job, item, identity)
                self._store_manifests(item)
                source_assets = self._register_source_assets(scene, item)
                if mode == "metadata_only":
                    skipped += 1
                    continue
                mirrored_assets = self._mirror_assets(item, source_assets)
                mirrored += len(mirrored_assets)
                actual_bytes += sum(asset.size_bytes or 0 for asset in mirrored_assets)
                if mode == "mirror_only":
                    skipped += 1
                    continue
                processed += self._process_scene(scene, item, mirrored_assets)
            except Exception as exc:
                failed += 1
                failed_items[item.stac_item_id] = str(exc)
        return LandsatBackfillSummary(
            searched_count=len(items),
            accepted_count=accepted,
            mirrored_asset_count=mirrored,
            processed_count=processed,
            skipped_count=skipped,
            failed_count=failed,
            actual_source_mirror_bytes=actual_bytes,
            stac_item_ids=accepted_ids,
            failed_items=failed_items,
        )

    def _register_scene(self, job: Job, item: NormalizedStacItem, identity) -> ProviderSceneRecord:
        return self._scene_repository.upsert(
            ProviderSceneRecord(
                id=None,
                provider_adapter=item.provider_adapter,
                source_id=LANDSAT_SOURCE_ID,
                provider_product_id=item.stac_item_id,
                acquisition_at=item.acquisition_at,
                scene_geometry=item.footprint,
                status="accepted",
                cloud_percent=item.cloud_percent,
                license_state="open",
                provider_metadata={
                    "provider_collection": LANDSAT_PROVIDER_COLLECTION,
                    "provider_route": LANDSAT_PRIMARY_PROVIDER_ROUTE,
                    "platform": identity.platform,
                    "product_type": identity.product_type,
                    "wrs_path": identity.wrs_path,
                    "wrs_row": identity.wrs_row,
                    "collection_number": identity.collection_number,
                    "collection_category": identity.collection_category,
                },
                aoi_id=job.aoi_id,
                logical_scene_key=item.logical_scene_key,
                native_resolution=LANDSAT_NATIVE_RESOLUTION_METERS,
            )
        )

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

    def _register_source_assets(
        self,
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
    ) -> list[SceneAssetRecord]:
        records: list[SceneAssetRecord] = []
        for asset_key in LANDSAT_REQUIRED_ASSETS:
            asset = item.assets[asset_key]
            records.append(
                self._asset_repository.upsert(
                    SceneAssetRecord(
                        id=None,
                        scene_id=scene.id or "",
                        asset_kind="source",
                        asset_key=asset_key,
                        asset_href=asset.href,
                        storage_backend=asset.storage_backend,
                        selected_access_mode=asset.selected_access_mode,
                        scale=asset.scale,
                        offset=asset.offset,
                        nodata_value=asset.nodata,
                        roles=asset.roles,
                        media_type=asset.media_type,
                        mirror_status="pending",
                    )
                )
            )
        return records

    def _mirror_assets(
        self,
        item: NormalizedStacItem,
        records: list[SceneAssetRecord],
    ) -> list[SceneAssetRecord]:
        service = self._mirroring_service or SourceMirroringService(
            object_store=self._object_store,
            settings=self._settings,
        )
        mirrored: list[SceneAssetRecord] = []
        for record in records:
            if record.mirror_status == "mirrored" and record.mirror_object_path:
                mirrored.append(record)
                continue
            asset = item.assets[record.asset_key or ""]
            signed_href = self._provider.signed_href(asset)
            result = service.mirror_asset(
                item=item,
                asset=asset,
                download_href=signed_href,
            )
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
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
        records: list[SceneAssetRecord],
    ) -> int:
        by_key = {record.asset_key or "": record for record in records}
        source_bands = {
            key: self._source_band(by_key[key])
            for key in (*LANDSAT_REFLECTANCE_ASSETS, "qa_pixel", "qa_radsat")
        }
        reference = source_bands["blue"]
        reflectance: dict[str, np.ndarray] = {}
        analytic_valid = np.ones(reference.values.shape, dtype=bool)
        for key in LANDSAT_REFLECTANCE_ASSETS:
            matched = _match_grid(source_bands[key], reference, Resampling.bilinear)
            converted = reflectance_from_dn(matched)
            reflectance[key] = converted
            analytic_valid &= np.isfinite(converted)
        qa_pixel = _match_grid(source_bands["qa_pixel"], reference, Resampling.nearest)
        qa_radsat = _match_grid(source_bands["qa_radsat"], reference, Resampling.nearest)
        mask = decode_qa_mask(
            qa_pixel.astype("uint16"),
            qa_radsat.astype("uint16"),
            analytic_valid_mask=analytic_valid,
        )
        analytic = np.stack([reflectance[key] for key in LANDSAT_REFLECTANCE_ASSETS])
        analytic = np.where(np.isfinite(analytic), analytic, _ANALYTIC_NODATA).astype("float32")
        common_tags = {
            "akasha:source_item_id": item.stac_item_id,
            "akasha:processing_profile_version": self._settings.landsat_profile_version,
            "akasha:mask_profile_version": self._settings.landsat_mask_profile_version,
        }
        analytic_payload = write_cog_bytes(
            analytic,
            transform=reference.transform,
            crs=reference.crs,
            nodata=_ANALYTIC_NODATA,
            tags=common_tags,
            band_descriptions=tuple(name.upper() for name in LANDSAT_REFLECTANCE_ASSETS),
            overview_resampling="average",
        )
        mask_payload = write_cog_bytes(
            mask,
            transform=reference.transform,
            crs=reference.crs,
            nodata=0,
            tags=common_tags,
            band_descriptions=("landsat-c2-qa-mask",),
            overview_resampling="nearest",
        )
        prepared_assets = [
            self._store_prepared_asset(
                scene,
                item,
                "analytic",
                analytic_payload,
                nodata=_ANALYTIC_NODATA,
                metadata={
                    "eo:bands": [
                        {"name": name.upper(), "common_name": name}
                        for name in LANDSAT_REFLECTANCE_ASSETS
                    ],
                    "raster:bands": [
                        {"data_type": "float32", "nodata": _ANALYTIC_NODATA}
                        for _ in LANDSAT_REFLECTANCE_ASSETS
                    ],
                },
            ),
            self._store_prepared_asset(
                scene,
                item,
                "mask",
                mask_payload,
                nodata=0,
                metadata={"classification:classes": [0, 1, 2, 3, 4, 5]},
            ),
        ]
        mask_path = prepared_assets[1].object_path or ""
        outputs: list[RasterOutputRecord] = []
        for index_name, (first_key, second_key) in LANDSAT_INDEX_ASSETS.items():
            valid = index_valid_mask(mask, reflectance[first_key], reflectance[second_key])
            values = calculate_index(
                index_name,
                reflectance[first_key],
                reflectance[second_key],
                valid_mask=valid,
            )
            profile = output_profile(index_name)
            encoded, _ = encode_index_output(index_name, values, profile=profile)
            metadata = cog_metadata(
                encoded,
                crs=reference.crs,
                resolution=LANDSAT_NATIVE_RESOLUTION_METERS,
                nodata=profile.nodata_value,
            )
            payload = write_cog_bytes(
                encoded,
                transform=reference.transform,
                crs=reference.crs,
                nodata=profile.nodata_value,
                tags={**common_tags, "akasha:formula_version": profile.formula_version},
                band_descriptions=(index_name.upper(),),
                overview_resampling="average",
            )
            object_path, checksum = self._object_store.put_derived_cog(
                provider=item.provider_adapter,
                source_id=item.source_id,
                stac_item_id=item.stac_item_id,
                index_name=index_name,
                payload=payload,
            )
            output = self._raster_repository.upsert_derived_index(
                RasterOutputRecord(
                    id=None,
                    scene_id=scene.id or "",
                    output_kind="derived_index",
                    index_name=index_name,
                    object_path=object_path,
                    checksum_sha256=checksum,
                    formula_version=profile.formula_version,
                    processing_profile_version=self._settings.landsat_profile_version,
                    dtype=profile.dtype,
                    scale_factor=profile.scale_factor,
                    nodata_value=profile.nodata_value,
                    min_value=metadata["min_value"],
                    max_value=metadata["max_value"],
                    native_resolution=LANDSAT_NATIVE_RESOLUTION_METERS,
                    processing_resolution=LANDSAT_NATIVE_RESOLUTION_METERS,
                    display_resolution=LANDSAT_NATIVE_RESOLUTION_METERS,
                    crs=reference.crs,
                    cloud_mask_version=self._settings.landsat_mask_profile_version,
                    metadata={
                        "pgstac_collection": LANDSAT_PGSTAC_COLLECTION_ID,
                        "pgstac_asset_key": index_name,
                        "pgstac_href": f"s3://{self._settings.minio_bucket}/{object_path}",
                        "mask_object_path": mask_path,
                    },
                )
            )
            outputs.append(output)
            self._tile_layer_repository.upsert_for_raster(
                TileLayerRecord(
                    layer_id=None,
                    raster_output_id=output.id or "",
                    visibility="private",
                    metadata={"index_name": index_name, "scene_id": scene.id},
                )
            )
        self._publish_scene(scene, item, prepared_assets, outputs)
        return 2 + len(outputs)

    def _store_prepared_asset(
        self,
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
        asset_key: str,
        payload: bytes,
        *,
        nodata: int | float,
        metadata: dict,
    ) -> SceneAssetRecord:
        object_path = (
            f"prepared/{item.provider_adapter}/{item.source_id}/{item.stac_item_id}/"
            f"{asset_key}.cog.tif"
        )
        object_path, checksum = self._object_store.put_bytes(
            object_path,
            payload,
            content_type="image/tiff; application=geotiff; profile=cloud-optimized",
        )
        return self._asset_repository.upsert(
            SceneAssetRecord(
                id=None,
                scene_id=scene.id or "",
                asset_kind="prepared",
                asset_key=asset_key,
                object_path=object_path,
                asset_href=f"s3://{self._settings.minio_bucket}/{object_path}",
                checksum_sha256=checksum,
                size_bytes=len(payload),
                storage_backend="minio",
                nodata_value=nodata,
                roles=["data", asset_key],
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                metadata=metadata,
            )
        )

    def _source_band(self, asset: SceneAssetRecord) -> RasterBand:
        if not asset.mirror_object_path:
            raise ValueError(f"asset is not mirrored: {asset.asset_key}")
        return read_single_band(self._object_store.get_required(asset.mirror_object_path))

    def _complete_outputs(self, scene: ProviderSceneRecord) -> list[RasterOutputRecord] | None:
        if not scene.id:
            return None
        outputs = self._raster_repository.list_for_scene_ids([scene.id])
        by_index = {output.index_name: output for output in outputs}
        if not all(index in by_index for index in LANDSAT_INDEX_ASSETS):
            return None
        prepared = {
            asset.asset_key for asset in self._asset_repository.list_for_scene(scene.id)
            if asset.asset_kind == "prepared"
        }
        if not {"analytic", "mask"}.issubset(prepared):
            return None
        return [by_index[index] for index in LANDSAT_INDEX_ASSETS]

    def _scene_is_complete(self, scene: ProviderSceneRecord) -> bool:
        return self._complete_outputs(scene) is not None and (
            self._pgstac_repository is None or scene.pgstac_item_id is not None
        )

    def _publish_scene(
        self,
        scene: ProviderSceneRecord,
        item: NormalizedStacItem,
        prepared_assets: list[SceneAssetRecord],
        outputs: list[RasterOutputRecord],
    ) -> None:
        if self._pgstac_repository is None or not scene.scene_geometry:
            return
        stac_item = build_landsat_derived_item(
            scene=scene,
            prepared_assets=prepared_assets,
            outputs=outputs,
            bbox=item.bbox,
            geometry=scene.scene_geometry,
        )
        self._pgstac_repository.upsert_item_json(stac_item)
        scene.pgstac_item_id = stac_item.id
        self._scene_repository.upsert(scene)

    def _upsert_summary(self, job: Job, summary: LandsatBackfillSummary) -> None:
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

    def _require_dependencies(self) -> None:
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
            raise RuntimeError(f"Landsat pipeline dependencies missing: {', '.join(missing)}")


def _match_grid(source: RasterBand, reference: RasterBand, resampling: Resampling) -> np.ndarray:
    if (
        source.values.shape == reference.values.shape
        and source.transform == reference.transform
        and source.crs == reference.crs
    ):
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


def _item_selection_key(item: NormalizedStacItem) -> tuple[float, str, str]:
    return (
        float(item.cloud_percent if item.cloud_percent is not None else 101.0),
        item.platform or "",
        item.stac_item_id,
    )


class _EmptyProvider:
    def search(self, request: ProviderSearchRequest) -> list[NormalizedStacItem]:
        del request
        return []

    def signed_href(self, asset: NormalizedAsset) -> str:
        return asset.href
