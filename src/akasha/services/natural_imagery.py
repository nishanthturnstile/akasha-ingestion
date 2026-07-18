from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from akasha.processing.eos04 import (
    EOS04_DEFAULT_RESCALE,
    EOS04_PGSTAC_COLLECTION_ID,
    EOS04_SOURCE_ID,
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
            asset = self._backscatter_asset(scene.id)
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
                    bounds=_bounds(asset.metadata.get("bbox")),
                    polarizations=[
                        str(value) for value in asset.metadata.get("polarizations", [])
                    ],
                    unavailableReason=(
                        None
                        if tile_available
                        else "Multiple same-date EOS-04 scenes require a SAR mosaic backend."
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
            raise NaturalImageryNotFound("EOS-04 acquisition date not found")
        if not metadata.tileAvailable:
            raise NaturalImageryUnavailable(
                metadata.unavailableReason or "EOS-04 tile is unavailable"
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
            raise NaturalImageryUnavailable("EOS-04 date does not resolve to exactly one scene")
        return self._tile_service.fetch_tile(
            collection_id=EOS04_PGSTAC_COLLECTION_ID,
            item_id=scenes[0].pgstac_item_id,
            z=z,
            x=x,
            y=y,
            assets="backscatter",
            asset_bidx="backscatter|1",
            rescale=EOS04_DEFAULT_RESCALE,
        )

    def _backscatter_asset(self, scene_id: str | None):
        if not scene_id:
            return None
        return next(
            (
                asset
                for asset in self._asset_repository.list_for_scene(scene_id)
                if asset.asset_key == "backscatter"
            ),
            None,
        )

    @staticmethod
    def _validate_source(source_id: str) -> None:
        if source_id != EOS04_SOURCE_ID:
            raise NaturalImageryNotFound("natural imagery source is not supported")


def _bounds(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return [float(item) for item in value]
