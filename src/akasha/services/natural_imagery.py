from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from akasha.catalog.pgstac_repository import PHASE2_DERIVED_COLLECTION_ID
from akasha.processing.eos04 import (
    EOS04_DEFAULT_RESCALE,
    EOS04_PGSTAC_COLLECTION_ID,
    EOS04_SOURCE_ID,
)
from akasha.processing.landsat import (
    LANDSAT_PGSTAC_COLLECTION_ID,
    LANDSAT_SOURCE_ID,
)
from akasha.processing.nisar import (
    NISAR_DEFAULT_RESCALE,
    NISAR_PGSTAC_COLLECTION_ID,
    NISAR_SOURCE_ID,
)
from akasha.schemas import (
    LatestImageryResult,
    LatestImagerySearch,
    NaturalSourceDate,
    SceneCandidate,
    SourceDatesResponse,
)

SENTINEL2_SOURCE_ID = "sentinel-2-l2a"
LATEST_IMAGE_POLICY_VERSION = "latest-image-s2-l2a-v1"


class NaturalImageryNotFound(LookupError):
    pass


class NaturalImageryUnavailable(RuntimeError):
    pass


class NaturalImageryService:
    def __init__(self, *, scene_repository: Any, asset_repository: Any, tile_service: Any) -> None:
        self._scene_repository = scene_repository
        self._asset_repository = asset_repository
        self._tile_service = tile_service

    def dates(self, *, source_id: str, aoi_id: str) -> SourceDatesResponse:
        self._validate_source(source_id)
        scenes = self._scene_repository.list_for_source_aoi(
            source_id=source_id,
            aoi_id=aoi_id,
        )
        grouped: dict[date, list[tuple[Any, Any]]] = defaultdict(list)
        for scene in scenes:
            if scene.status != "accepted" or scene.acquisition_at is None:
                continue
            asset = self._display_asset(scene.id, source_id)
            if asset is None or not scene.pgstac_item_id:
                continue
            grouped[scene.acquisition_at.date()].append((scene, asset))

        results: list[NaturalSourceDate] = []
        for acquisition_date in sorted(grouped, reverse=True):
            entries = grouped[acquisition_date]
            scene, asset = entries[0]
            tile_available = len(entries) == 1
            results.append(
                NaturalSourceDate(
                    acquisitionDate=acquisition_date,
                    datetime=scene.acquisition_at,
                    tileAvailable=tile_available,
                    sceneCount=len(entries),
                    bounds=(
                        _bounds(asset.metadata.get("bbox"))
                        or _geometry_bounds(scene.scene_geometry)
                    ),
                    polarizations=[str(value) for value in asset.metadata.get("polarizations", [])],
                    unavailableReason=(
                        None
                        if tile_available
                        else (
                            f"Multiple same-date {_source_label(source_id)} scenes require "
                            f"a {'SAR ' if source_id != LANDSAT_SOURCE_ID else ''}mosaic backend."
                        )
                    ),
                )
            )
        return SourceDatesResponse(sourceId=source_id, aoiId=aoi_id, dates=results)

    def tile(
        self,
        *,
        source_id: str,
        aoi_id: str,
        acquisition_date: date,
        z: int,
        x: int,
        y: int,
    ) -> tuple[bytes, str]:
        dates = self.dates(source_id=source_id, aoi_id=aoi_id)
        metadata = next(
            (entry for entry in dates.dates if entry.acquisitionDate == acquisition_date),
            None,
        )
        if metadata is None:
            raise NaturalImageryNotFound(f"{_source_label(source_id)} acquisition date not found")
        if not metadata.tileAvailable:
            raise NaturalImageryUnavailable(
                metadata.unavailableReason or f"{_source_label(source_id)} tile is unavailable"
            )
        scenes = self._scene_repository.list_candidates(
            source_id=source_id,
            requested_date=acquisition_date,
            window_days=0,
            max_cloud_percentage=100,
            limit=2,
        )
        scenes = [scene for scene in scenes if scene.aoi_id == aoi_id and scene.pgstac_item_id]
        if len(scenes) != 1:
            raise NaturalImageryUnavailable(
                f"{_source_label(source_id)} date does not resolve to exactly one scene"
            )
        profile = _source_profile(source_id)
        polarizations = metadata.polarizations
        band_index = polarizations.index("HH") + 1 if "HH" in polarizations else 1
        return self._tile_service.fetch_tile(
            collection_id=profile["collection_id"],
            item_id=scenes[0].pgstac_item_id,
            z=z,
            x=x,
            y=y,
            assets=profile["asset_key"],
            asset_bidx=(
                profile["asset_bidx"]
                if source_id == LANDSAT_SOURCE_ID
                else f"backscatter|{band_index}"
            ),
            rescale=profile["rescale"],
        )

    def search(self, request: LatestImagerySearch) -> LatestImageryResult:
        end_date = datetime.now(UTC).date()
        start_date = end_date - timedelta(days=request.lookbackDays - 1)
        candidates: list[SceneCandidate] = []
        for scene, coverage in self._scene_repository.list_spatial(
            source_id=request.sourceId,
            viewport=request.viewport,
            start_date=start_date,
            end_date=end_date,
            max_cloud_percentage=request.maxCloudPercent,
            limit=request.limit,
        ):
            if scene.id is None or scene.acquisition_at is None or scene.scene_geometry is None:
                continue
            assets = self._display_assets(scene.id, request.sourceId)
            renderable = bool(assets) and bool(scene.pgstac_item_id)
            full_coverage = coverage >= 99.9
            bounds = next(
                (
                    candidate_bounds
                    for asset in assets
                    if asset.metadata
                    and (candidate_bounds := _bounds(asset.metadata.get("bbox"))) is not None
                ),
                None,
            )
            bounds = bounds or _geometry_bounds(scene.scene_geometry)
            if bounds is None:
                continue
            reason = None
            if not renderable:
                reason = "Prepared RGB imagery is unavailable."
            elif not full_coverage:
                reason = "Scene does not fully cover the searched viewport."
            candidates.append(
                SceneCandidate(
                    sceneId=scene.id,
                    acquisitionDate=scene.acquisition_at.date(),
                    acquisitionDatetime=scene.acquisition_at,
                    sourceId=scene.source_id,
                    sensor="Sentinel-2",
                    processingLevel=request.processingLevel,
                    cloudPercent=float(scene.cloud_percent or 0.0),
                    coveragePercent=round(coverage, 3),
                    coverageStatus="full" if full_coverage else "partial",
                    usable=renderable and full_coverage,
                    bounds=bounds,
                    unavailableReason=reason,
                )
            )
        return LatestImageryResult(
            policyVersion=LATEST_IMAGE_POLICY_VERSION,
            searchedAt=datetime.now(UTC),
            candidates=candidates,
        )

    def scene_tile(self, *, scene_id: str, z: int, x: int, y: int) -> tuple[bytes, str]:
        scene = self._scene_repository.get(scene_id)
        if scene is None or scene.source_id != SENTINEL2_SOURCE_ID:
            raise NaturalImageryNotFound("latest imagery scene not found")
        assets = self._display_assets(scene_id, scene.source_id)
        if not assets or not scene.pgstac_item_id:
            raise NaturalImageryUnavailable("latest imagery scene is not renderable")
        profile = _source_profile(scene.source_id)
        return self._tile_service.fetch_tile(
            collection_id=profile["collection_id"],
            item_id=scene.pgstac_item_id,
            z=z,
            x=x,
            y=y,
            assets="red,green,blue",
            asset_bidx=None,
            rescale=profile["rescale"],
        )

    def scene_thumbnail(self, *, scene_id: str) -> tuple[bytes, str]:
        scene = self._scene_repository.get(scene_id)
        if scene is None or scene.scene_geometry is None:
            raise NaturalImageryNotFound("latest imagery scene not found")
        bounds = _geometry_bounds(scene.scene_geometry)
        if bounds is None:
            raise NaturalImageryUnavailable("latest imagery scene has no footprint")
        lon = (bounds[0] + bounds[2]) / 2
        lat = (bounds[1] + bounds[3]) / 2
        z = 10
        scale = 2**z
        x = int((lon + 180.0) / 360.0 * scale)
        lat_rad = math.radians(max(-85.0511, min(85.0511, lat)))
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale)
        return self.scene_tile(scene_id=scene_id, z=z, x=x, y=y)

    def _display_asset(self, scene_id: str | None, source_id: str):
        assets = self._display_assets(scene_id, source_id)
        return assets[0] if assets else None

    def _display_assets(self, scene_id: str | None, source_id: str) -> list[Any]:
        if not scene_id:
            return []
        scene_assets = self._asset_repository.list_for_scene(scene_id)
        if source_id == SENTINEL2_SOURCE_ID:
            rgb = {
                asset.asset_key: asset
                for asset in scene_assets
                if asset.asset_key in {"red", "green", "blue"}
                and asset.mirror_status == "mirrored"
                and asset.mirror_object_path
            }
            if len(rgb) == 3:
                return [rgb[key] for key in ("red", "green", "blue")]
            # Transitional support for already prepared RGB composite records.
            return [
                asset
                for asset in scene_assets
                if asset.asset_key == "analytic" and asset.asset_kind in {"analytic", "prepared"}
            ]
        optical = source_id == LANDSAT_SOURCE_ID
        asset_key = "analytic" if optical else "backscatter"
        return [
            asset
            for asset in scene_assets
            if asset.asset_key == asset_key and asset.asset_kind in {"analytic", "prepared"}
        ]

    @staticmethod
    def _validate_source(source_id: str) -> None:
        if source_id not in {
            EOS04_SOURCE_ID,
            NISAR_SOURCE_ID,
            LANDSAT_SOURCE_ID,
            SENTINEL2_SOURCE_ID,
        }:
            raise NaturalImageryNotFound("natural imagery source is not supported")


def _bounds(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return [float(item) for item in value]


def _geometry_bounds(value: Any) -> list[float] | None:
    if not isinstance(value, dict):
        return None
    coordinates = value.get("coordinates")
    points: list[tuple[float, float]] = []

    def collect(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            points.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                collect(child)

    collect(coordinates)
    if not points:
        return None
    xs, ys = zip(*points, strict=True)
    return [min(xs), min(ys), max(xs), max(ys)]


def _source_profile(source_id: str) -> dict[str, str]:
    if source_id == EOS04_SOURCE_ID:
        return {
            "collection_id": EOS04_PGSTAC_COLLECTION_ID,
            "asset_key": "backscatter",
            "asset_bidx": "backscatter|1",
            "rescale": EOS04_DEFAULT_RESCALE,
        }
    if source_id == NISAR_SOURCE_ID:
        return {
            "collection_id": NISAR_PGSTAC_COLLECTION_ID,
            "asset_key": "backscatter",
            "asset_bidx": "backscatter|1",
            "rescale": NISAR_DEFAULT_RESCALE,
        }
    if source_id == LANDSAT_SOURCE_ID:
        return {
            "collection_id": LANDSAT_PGSTAC_COLLECTION_ID,
            "asset_key": "analytic",
            "asset_bidx": "analytic|3,2,1",
            "rescale": "0,0.3",
        }
    if source_id == SENTINEL2_SOURCE_ID:
        return {
            "collection_id": PHASE2_DERIVED_COLLECTION_ID,
            "asset_key": "analytic",
            "asset_bidx": "analytic|1,8,9",
            "rescale": "0,3000",
        }
    raise NaturalImageryNotFound("natural imagery source is not supported")


def _source_label(source_id: str) -> str:
    if source_id == NISAR_SOURCE_ID:
        return "NISAR"
    if source_id == LANDSAT_SOURCE_ID:
        return "Landsat 8/9"
    return "EOS-04"
