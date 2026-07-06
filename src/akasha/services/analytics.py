from __future__ import annotations

from pyproj import Geod
from shapely.errors import ShapelyError
from shapely.geometry import Point, shape

from akasha.catalog.field_query_repository import FieldQueryRecord, new_query_id
from akasha.catalog.tile_layer_repository import TileLayerRecord
from akasha.config import Settings
from akasha.processing.raster_stats import raster_stats
from akasha.schemas import (
    FieldIndexAvailableResponse,
    FieldIndexPointResponse,
    FieldIndexQuality,
    FieldIndexRequest,
    FieldIndexResolution,
    FieldIndexResponse,
    FieldIndexSelection,
    FieldIndexStatistics,
    FieldIndexUnavailableResponse,
    FieldIndexVisualization,
)
from akasha.services.signing import SigningService


class AnalyticsService:
    def __init__(
        self,
        *,
        field_query_repository,
        settings: Settings,
        scene_repository=None,
        raster_repository=None,
        tile_layer_repository=None,
        object_store=None,
        profile_repository=None,
        signing_service: SigningService | None = None,
    ) -> None:
        self._field_query_repository = field_query_repository
        self._settings = settings
        self._scene_repository = scene_repository
        self._raster_repository = raster_repository
        self._tile_layer_repository = tile_layer_repository
        self._object_store = object_store
        self._profile_repository = profile_repository
        self._signing = signing_service or SigningService(settings)

    def field_index(self, request: FieldIndexRequest) -> FieldIndexResponse:
        self._validate_geometry_limits(request)
        if not self._has_available_dependencies():
            return self._unavailable(
                request,
                "Field-index selection dependencies are not configured",
            )

        candidates = self._scene_repository.list_candidates(
            source_id="sentinel-2-l2a",
            requested_date=request.date,
            window_days=7,
            max_cloud_percentage=request.maxCloudPercentage,
            limit=self._settings.max_candidate_scenes,
        )
        for scene in candidates:
            raster = self._raster_repository.get_for_scene_index(
                scene_id=scene.id or "",
                index_name=request.index,
            )
            if raster is None:
                continue
            threshold = (
                self._profile_repository.get_default_threshold(
                    request.index,
                    source_id="sentinel-2-l2a",
                )
                if self._profile_repository is not None
                else None
            )
            visualization = (
                self._profile_repository.get_default_visualization(request.index)
                if self._profile_repository is not None
                else None
            )
            stats, class_stats = raster_stats(
                self._object_store.get_required(raster.object_path),
                geometry=request.geometry,
                encoded_nodata=raster.nodata_value,
                scale_factor=raster.scale_factor,
                threshold_classes=threshold.classes_json if threshold else [],
            )
            valid_pixels = int(stats.pop("validPixelCount", 0) or 0)
            if valid_pixels < self._settings.field_min_usable_pixels:
                continue
            usable = float(stats["usablePixelPercentage"] or 0.0) / 100
            if usable < self._settings.field_usable_pixel_threshold:
                continue

            layer = self._tile_layer_repository.upsert_for_raster(
                TileLayerRecord(
                    layer_id=None,
                    raster_output_id=raster.id or "",
                    visibility="private",
                    metadata={"index_name": request.index.lower(), "scene_id": scene.id},
                )
            )
            query_id = new_query_id()
            tile_template = f"/tiles/{layer.layer_id}/{{z}}/{{x}}/{{y}}.png"
            tile_ref = self._signing.sign(
                method="GET",
                operation="tile",
                resource_id=layer.layer_id or "",
                path_template=tile_template,
                geometry_or_query_hash=self._signing.query_hash(f"{layer.layer_id}:tile"),
            )
            stats_template = f"/api/v1/analytics/field-index/{query_id}"
            stats_ref = self._signing.sign(
                method="GET",
                operation="stats",
                resource_id=query_id,
                path_template=stats_template,
                geometry_or_query_hash=self._signing.query_hash(f"{query_id}:stats"),
            )
            overlay_template = f"/api/v1/analytics/field-index/{query_id}/overlay.png"
            overlay_ref = self._signing.sign(
                method="GET",
                operation="overlay",
                resource_id=query_id,
                path_template=overlay_template,
                geometry_or_query_hash=self._signing.query_hash(f"{query_id}:overlay"),
            )
            point_template = f"/api/v1/analytics/field-index/{query_id}/point"
            point_ref = self._signing.sign(
                method="GET",
                operation="point",
                resource_id=query_id,
                path_template=point_template,
                geometry_or_query_hash=self._signing.query_hash(f"{query_id}:point"),
            )
            record = self._field_query_repository.save(
                FieldQueryRecord(
                    query_id=query_id,
                    field_geometry=request.geometry,
                    index_name=request.index.lower(),
                    requested_date=request.date,
                    selected_scene_id=scene.id,
                    raster_output_id=raster.id,
                    layer_id=layer.layer_id,
                    valid_pixel_count=valid_pixels,
                    selection_reason="quality_first",
                    stats_json={**stats, "cloudPercentage": scene.cloud_percent},
                    class_area_json=class_stats,
                    quality_json={"status": "GOOD", "reason": "Field cloud cover within threshold"},
                    visualization_profile_id=visualization.id if visualization else None,
                    threshold_profile_id=threshold.id if threshold else None,
                )
            )
            return FieldIndexAvailableResponse(
                queryId=record.query_id,
                fieldId=request.fieldId,
                index=request.index,
                requestedDate=request.date,
                selectedSceneDate=(
                    scene.acquisition_at.date() if scene.acquisition_at else request.date
                ),
                source=scene.source_id,
                providerRoute=scene.provider_metadata.get(
                    "provider_route",
                    "earthsearch:sentinel-2-l2a",
                ),
                resolution=FieldIndexResolution(
                    nativeMeters=raster.native_resolution or raster.processing_resolution or 10,
                    processingMeters=raster.processing_resolution or 10,
                    displayMeters=raster.display_resolution or raster.processing_resolution or 10,
                ),
                layerId=layer.layer_id or "",
                tileUrl=f"{self._settings.public_base_url}{tile_template}?{tile_ref.query_string()}",
                statsUrl=f"{self._settings.public_base_url}{stats_template}?{stats_ref.query_string()}",
                overlayUrl=(
                    f"{self._settings.public_base_url}{overlay_template}"
                    f"?{overlay_ref.query_string()}"
                ),
                pointUrl=(
                    f"{self._settings.public_base_url}{point_template}"
                    f"?{point_ref.query_string()}"
                ),
                selection=FieldIndexSelection(
                    windowDays=7,
                    rule="quality_first",
                    validPixelCount=valid_pixels,
                ),
                statistics=FieldIndexStatistics(
                    min=stats["min"],
                    max=stats["max"],
                    mean=stats["mean"],
                    median=stats["median"],
                    stdDev=stats["stdDev"],
                    usablePixelPercentage=float(stats["usablePixelPercentage"] or 0.0),
                    cloudPercentage=scene.cloud_percent,
                ),
                classStatistics=class_stats,
                visualization=FieldIndexVisualization(
                    displayProfile=visualization.version if visualization else None,
                    thresholdProfile=threshold.version if threshold else None,
                    legend=visualization.palette_json if visualization else [],
                ),
                versions={
                    "atmosphericCorrection": "vendor-l2a",
                    "cloudMask": raster.cloud_mask_version or "scl-v1",
                    "formula": raster.formula_version or "",
                    "displayProfile": visualization.version if visualization else "",
                    "thresholdProfile": threshold.version if threshold else "",
                },
                quality=FieldIndexQuality(
                    status="GOOD",
                    reason="Field cloud cover within threshold",
                    warnings=[],
                ),
            )

        return self._unavailable(
            request,
            "No optical scene with field usable-pixels >= 80% within +/- 7 days",
        )

    def stats_for_query(self, query_id: str) -> dict[str, object] | None:
        record = self._field_query_repository.get(query_id)
        if record is None:
            return None
        return {
            "queryId": record.query_id,
            "statistics": record.stats_json,
            "classStatistics": record.class_area_json,
            "quality": record.quality_json,
        }

    def overlay_for_query(self, query_id: str) -> tuple[bytes, list[list[float]] | None] | None:
        """Render a field-clipped index overlay PNG for a stored query.

        Returns ``(png_bytes, corners)`` or ``None`` when the query, its raster
        output, or object storage is unavailable. Corners are ``[lng, lat]``
        pairs for a MapLibre ``image`` source; ``None`` when the polygon has no
        valid pixels (a transparent PNG is still returned).
        """

        from akasha.processing.overlay import render_clipped_index_overlay

        record = self._field_query_repository.get(query_id)
        if record is None or not record.raster_output_id:
            return None
        if self._raster_repository is None or self._object_store is None:
            return None
        raster = self._raster_repository.get(record.raster_output_id)
        if raster is None:
            return None
        payload = self._object_store.get_required(raster.object_path)
        return render_clipped_index_overlay(
            payload,
            geometry=record.field_geometry,
            index_name=record.index_name,
            scale_factor=raster.scale_factor,
            nodata=raster.nodata_value,
        )

    def point_for_query(
        self,
        query_id: str,
        lng: float,
        lat: float,
    ) -> FieldIndexPointResponse | None:
        record = self._field_query_repository.get(query_id)
        if record is None or not record.raster_output_id:
            return None
        if self._raster_repository is None or self._object_store is None:
            return None
        raster = self._raster_repository.get(record.raster_output_id)
        if raster is None:
            return None

        payload = self._object_store.get_required(raster.object_path)
        value, masked, mask_class = _sample_point(
            payload,
            geometry=record.field_geometry,
            lng=lng,
            lat=lat,
            scale_factor=raster.scale_factor,
            nodata=raster.nodata_value,
        )
        return FieldIndexPointResponse(
            queryId=record.query_id,
            index=record.index_name.upper(),
            lng=lng,
            lat=lat,
            value=value,
            masked=masked,
            maskClass=mask_class,
            source=self._source_for_query(record, raster),
        )

    def _source_for_query(self, record: FieldQueryRecord, raster: object) -> str:
        scene_id = record.selected_scene_id or getattr(raster, "scene_id", None)
        if self._scene_repository is not None and scene_id:
            scene = self._scene_repository.get(scene_id)
            if scene is not None:
                return scene.source_id
        return "sentinel-2-l2a"

    def _unavailable(
        self,
        request: FieldIndexRequest,
        reason: str,
    ) -> FieldIndexUnavailableResponse:
        query_id = new_query_id()
        self._field_query_repository.save(
            FieldQueryRecord(
                query_id=query_id,
                field_geometry=request.geometry,
                index_name=request.index.lower(),
                requested_date=request.date,
                selection_reason=reason,
                stats_json={},
                quality_json={"status": "UNAVAILABLE", "reason": reason},
            )
        )
        return FieldIndexUnavailableResponse(
            index=request.index,
            requestedDate=request.date,
            reason=reason,
            searchedSources=["sentinel-2-l2a"],
        )

    def _has_available_dependencies(self) -> bool:
        return all(
            value is not None
            for value in (
                self._scene_repository,
                self._raster_repository,
                self._tile_layer_repository,
                self._object_store,
            )
        )

    def _validate_geometry_limits(self, request: FieldIndexRequest) -> None:
        try:
            geometry = shape(request.geometry)
        except (TypeError, ValueError, ShapelyError) as exc:
            raise ValueError("geometry coordinates are invalid") from exc
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("geometry must be valid and non-empty")
        if _vertex_count(request.geometry) > self._settings.field_max_vertices:
            raise ValueError("geometry exceeds maximum vertex count")
        area_sq_km = abs(_GEOD.geometry_area_perimeter(geometry)[0]) / 1_000_000
        if area_sq_km > self._settings.field_max_area_sq_km:
            raise ValueError("geometry exceeds maximum field area")


def _vertex_count(geometry: dict) -> int:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        return sum(len(ring) for ring in coordinates)
    if geometry_type == "MultiPolygon":
        return sum(len(ring) for polygon in coordinates for ring in polygon)
    return 0


_GEOD = Geod(ellps="WGS84")


def _sample_point(
    payload: bytes,
    *,
    geometry: dict,
    lng: float,
    lat: float,
    scale_factor: float | None,
    nodata: int | float | None,
) -> tuple[float | None, bool, int | None]:
    import numpy as np
    from pyproj import CRS, Transformer
    from rasterio.io import MemoryFile

    field_geometry = shape(geometry)
    point = Point(lng, lat)
    if field_geometry.is_empty or not field_geometry.covers(point):
        return None, True, None

    with MemoryFile(payload) as memory_file, memory_file.open() as dataset:
        x, y = lng, lat
        if dataset.crs is not None and CRS.from_user_input(dataset.crs) != CRS.from_epsg(4326):
            transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
            x, y = transformer.transform(lng, lat)
        row, col = dataset.index(x, y)
        if row < 0 or col < 0 or row >= dataset.height or col >= dataset.width:
            return None, True, None

        raw_value = dataset.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]
        if not np.isfinite(raw_value):
            return None, True, None
        if dataset.nodata is not None and raw_value == dataset.nodata:
            return None, True, None
        if nodata is not None and raw_value == nodata:
            return None, True, None

    value = float(raw_value)
    if scale_factor is not None and scale_factor != 0:
        value /= float(scale_factor)
    return round(value, 6), False, None
