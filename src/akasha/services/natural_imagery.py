from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

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
from akasha.schemas import NaturalSourceDate, SourceDatesResponse


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
                    polarizations=[
                        str(value) for value in asset.metadata.get("polarizations", [])
                    ],
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

    def _display_asset(self, scene_id: str | None, source_id: str):
        if not scene_id:
            return None
        asset_key = "analytic" if source_id == LANDSAT_SOURCE_ID else "backscatter"
        return next(
            (
                asset
                for asset in self._asset_repository.list_for_scene(scene_id)
                if asset.asset_key == asset_key and asset.asset_kind in {"analytic", "prepared"}
            ),
            None,
        )

    @staticmethod
    def _validate_source(source_id: str) -> None:
        if source_id not in {EOS04_SOURCE_ID, NISAR_SOURCE_ID, LANDSAT_SOURCE_ID}:
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
    raise NaturalImageryNotFound("natural imagery source is not supported")


def _source_label(source_id: str) -> str:
    if source_id == NISAR_SOURCE_ID:
        return "NISAR"
    if source_id == LANDSAT_SOURCE_ID:
        return "Landsat 8/9"
    return "EOS-04"
