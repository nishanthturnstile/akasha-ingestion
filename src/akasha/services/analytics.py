from __future__ import annotations

from dataclasses import dataclass

from pyproj import Geod
from rasterio.errors import RasterioError
from shapely.errors import ShapelyError
from shapely.geometry import Point, shape

from akasha.catalog.field_query_repository import FieldQueryRecord, new_query_id
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.catalog.tile_layer_repository import TileLayerRecord
from akasha.config import Settings
from akasha.processing.raster_source import RasterSource, open_raster
from akasha.processing.raster_stats import categorical_mask_stats, raster_stats
from akasha.processing.resourcesat import (
    RESOURCESAT_FORMULA_VERSION,
    RESOURCESAT_MASK_METHOD,
    RESOURCESAT_PROFILES,
    has_exact_date_composite_provenance,
)
from akasha.processing.sentinel2 import SENTINEL2_FORMULA_VERSION, SENTINEL2_INDEX_ASSETS
from akasha.schemas import (
    FieldDateAvailability,
    FieldDatesRequest,
    FieldDatesResponse,
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
from akasha.storage.object_store import ObjectStoreNotFoundError, ObjectStoreReadError


class AnalyticsRasterNotFound(FileNotFoundError):
    pass


class AnalyticsRasterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _FieldIndexCandidate:
    scene: ProviderSceneRecord
    raster: RasterOutputRecord
    stats: dict[str, object]
    class_stats: list[dict[str, object]]
    valid_pixels: int
    usable_pixel_percentage: float
    cloud_percentage: float
    field_coverage_percentage: float
    shadow_percentage: float
    obscured_percentage: float
    threshold: object | None
    visualization: object | None


class AnalyticsService:
    def __init__(
        self,
        *,
        field_query_repository,
        settings: Settings,
        scene_repository=None,
        raster_repository=None,
        asset_repository=None,
        tile_layer_repository=None,
        object_store=None,
        profile_repository=None,
        signing_service: SigningService | None = None,
    ) -> None:
        self._field_query_repository = field_query_repository
        self._settings = settings
        self._scene_repository = scene_repository
        self._raster_repository = raster_repository
        self._asset_repository = asset_repository
        self._tile_layer_repository = tile_layer_repository
        self._object_store = object_store
        self._profile_repository = profile_repository
        self._signing = signing_service or SigningService(settings)

    def field_index(self, request: FieldIndexRequest) -> FieldIndexResponse:
        self._validate_geometry_limits(request)
        source_id = request.sourceId
        index_name = request.index.lower()
        self._validate_source_index(source_id, index_name)
        if not self._has_available_dependencies():
            return self._unavailable(
                request,
                "Field-index selection dependencies are not configured",
            )

        window_days = _field_index_window_days(source_id, self._settings)
        options, raster_failure_count = self._candidate_options(
            request,
            window_days=window_days,
        )

        if options:
            selected = min(options, key=lambda option: _candidate_quality_key(option, request.date))
            scene = selected.scene
            raster = selected.raster
            stats = selected.stats
            class_stats = selected.class_stats
            threshold = selected.threshold
            visualization = selected.visualization
            valid_pixels = selected.valid_pixels
            warnings = _quality_warnings(source_id)
            quality_status = "WARN" if warnings else "GOOD"
            quality_reason = (
                "Field usable pixels satisfy threshold with source-specific warnings"
                if warnings
                else "Field usable pixels satisfy threshold"
            )
            layer = self._tile_layer_repository.upsert_for_raster(
                TileLayerRecord(
                    layer_id=None,
                    raster_output_id=raster.id or "",
                    visibility="private",
                    metadata={"index_name": index_name, "scene_id": scene.id},
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
                    index_name=index_name,
                    requested_date=request.date,
                    selected_scene_id=scene.id,
                    raster_output_id=raster.id,
                    layer_id=layer.layer_id,
                    valid_pixel_count=valid_pixels,
                    selection_reason="source_aware_quality_first",
                    stats_json={**stats, "cloudPercentage": selected.cloud_percentage},
                    class_area_json=class_stats,
                    quality_json={
                        "status": quality_status,
                        "reason": quality_reason,
                        "warnings": warnings,
                    },
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
                providerRoute=_provider_route_for_scene(scene),
                resolution=FieldIndexResolution(
                    nativeMeters=_native_resolution(scene, raster, source_id),
                    processingMeters=(
                        raster.processing_resolution or _native_resolution(scene, raster, source_id)
                    ),
                    displayMeters=(
                        raster.display_resolution
                        or raster.processing_resolution
                        or _native_resolution(scene, raster, source_id)
                    ),
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
                    windowDays=window_days,
                    rule="source_aware_quality_first",
                    validPixelCount=valid_pixels,
                ),
                statistics=FieldIndexStatistics(
                    min=stats["min"],
                    max=stats["max"],
                    mean=stats["mean"],
                    median=stats["median"],
                    stdDev=stats["stdDev"],
                    usablePixelPercentage=float(stats["usablePixelPercentage"] or 0.0),
                    cloudPercentage=selected.cloud_percentage,
                    fieldCoveragePercentage=selected.field_coverage_percentage,
                    shadowPercentage=selected.shadow_percentage,
                    obscuredPercentage=selected.obscured_percentage,
                ),
                classStatistics=class_stats,
                visualization=FieldIndexVisualization(
                    displayProfile=visualization.version if visualization else None,
                    thresholdProfile=threshold.version if threshold else None,
                    legend=visualization.palette_json if visualization else [],
                ),
                versions={
                    "atmosphericCorrection": _atmospheric_correction_version(source_id),
                    "cloudMask": raster.cloud_mask_version or _default_cloud_mask(source_id),
                    "formula": raster.formula_version or _default_formula_version(
                        source_id,
                        index_name,
                    ),
                    "displayProfile": visualization.version if visualization else "",
                    "thresholdProfile": threshold.version if threshold else "",
                },
                quality=FieldIndexQuality(
                    status=quality_status,
                    reason=quality_reason,
                    warnings=warnings,
                ),
            )

        if raster_failure_count:
            raise AnalyticsRasterUnavailable(
                "Candidate raster outputs are temporarily unavailable."
            )

        return self._unavailable(
            request,
            (
                "No optical scene with field usable-pixels >= 80% within "
                f"+/- {window_days} days"
            ),
        )

    def field_dates(self, request: FieldDatesRequest) -> FieldDatesResponse:
        self._validate_geometry_limits(request)
        self._validate_source_index(request.sourceId, request.index.lower())
        if not self._has_available_dependencies():
            raise AnalyticsRasterUnavailable(
                "Field-date selection dependencies are not configured."
            )

        results: list[FieldDateAvailability] = []
        for acquisition_date in sorted(request.dates, reverse=True):
            candidate_request = FieldIndexRequest(
                geometry=request.geometry,
                sourceId=request.sourceId,
                crs=request.crs,
                index=request.index,
                date=acquisition_date,
                maxCloudPercentage=request.maxCloudPercentage,
            )
            options, raster_failure_count = self._candidate_options(
                candidate_request,
                window_days=0,
            )
            if options:
                selected = min(
                    options,
                    key=lambda option: _candidate_quality_key(option, acquisition_date),
                )
                results.append(
                    FieldDateAvailability(
                        acquisitionDate=acquisition_date,
                        available=True,
                        selectedSceneDate=(
                            selected.scene.acquisition_at.date()
                            if selected.scene.acquisition_at
                            else acquisition_date
                        ),
                        usablePixelPercentage=selected.usable_pixel_percentage,
                        cloudPercentage=selected.cloud_percentage,
                        fieldCoveragePercentage=selected.field_coverage_percentage,
                        shadowPercentage=selected.shadow_percentage,
                        obscuredPercentage=selected.obscured_percentage,
                        validPixelCount=selected.valid_pixels,
                    )
                )
                continue
            if raster_failure_count:
                raise AnalyticsRasterUnavailable(
                    "Candidate raster outputs are temporarily unavailable."
                )
            results.append(
                FieldDateAvailability(
                    acquisitionDate=acquisition_date,
                    available=False,
                    reason="No exact-date scene satisfies field quality thresholds.",
                )
            )

        return FieldDatesResponse(
            sourceId=request.sourceId,
            index=request.index,
            dates=results,
        )

    def _candidate_options(
        self,
        request: FieldIndexRequest,
        *,
        window_days: int,
    ) -> tuple[list[_FieldIndexCandidate], int]:
        source_id = request.sourceId
        index_name = request.index.lower()
        max_cloud_percentage = min(
            request.maxCloudPercentage,
            self._settings.field_max_cloud_percentage,
        )
        candidates = sorted(
            self._scene_repository.list_candidates(
                source_id=source_id,
                requested_date=request.date,
                window_days=window_days,
                max_cloud_percentage=max_cloud_percentage,
                limit=self._settings.max_candidate_scenes,
            ),
            key=lambda scene: _candidate_prefilter_key(scene, source_id, request.date),
        )
        options: list[_FieldIndexCandidate] = []
        raster_failure_count = 0
        for scene in candidates:
            if source_id in RESOURCESAT_PROFILES and not has_exact_date_composite_provenance(
                scene.acquisition_at,
                scene.provider_metadata,
            ):
                continue
            raster = self._raster_repository.get_for_scene_index(
                scene_id=scene.id or "",
                index_name=index_name,
            )
            if raster is None:
                continue
            threshold = (
                self._profile_repository.get_default_threshold(
                    index_name,
                    source_id=source_id,
                )
                if self._profile_repository is not None
                else None
            )
            visualization = (
                self._profile_repository.get_default_visualization(index_name)
                if self._profile_repository is not None
                else None
            )
            try:
                stats, class_stats = raster_stats(
                    self._object_store.raster_source(raster.object_path),
                    geometry=request.geometry,
                    encoded_nodata=raster.nodata_value,
                    scale_factor=raster.scale_factor,
                    threshold_classes=threshold.classes_json if threshold else [],
                )
                mask_source = self._mask_source(scene, raster)
                if mask_source is None:
                    continue
                mask_stats = categorical_mask_stats(
                    mask_source,
                    geometry=request.geometry,
                    **_mask_class_policy(source_id),
                )
            except (ObjectStoreNotFoundError, ObjectStoreReadError, RasterioError):
                raster_failure_count += 1
                continue
            mask_usable_pixels = int(mask_stats["usablePixelCount"] or 0)
            analytic_valid_pixels = int(stats["validPixelCount"] or 0)
            valid_pixels = min(mask_usable_pixels, analytic_valid_pixels)
            # A date is usable only where both the categorical quality mask and
            # the derived analytic raster contain usable field pixels.  Taking
            # the lower percentage also prevents a clear mask from admitting an
            # index COG that is entirely nodata (or only partially populated).
            usable_percentage = min(
                float(mask_stats["usablePixelPercentage"] or 0.0),
                float(stats["usablePixelPercentage"] or 0.0),
            )
            field_coverage = float(mask_stats["fieldCoveragePercentage"] or 0.0)
            cloud_percentage = float(mask_stats["cloudPercentage"] or 0.0)
            shadow_percentage = float(mask_stats["shadowPercentage"] or 0.0)
            obscured_percentage = float(mask_stats["obscuredPercentage"] or 0.0)
            stats.update(mask_stats)
            stats["validPixelCount"] = valid_pixels
            if valid_pixels < self._settings.field_min_usable_pixels:
                continue
            if field_coverage < self._settings.field_min_coverage_percentage:
                continue
            if usable_percentage / 100 < self._settings.field_usable_pixel_threshold:
                continue
            if obscured_percentage >= max_cloud_percentage:
                continue
            options.append(
                _FieldIndexCandidate(
                    scene=scene,
                    raster=raster,
                    stats=stats,
                    class_stats=class_stats,
                    valid_pixels=valid_pixels,
                    usable_pixel_percentage=usable_percentage,
                    cloud_percentage=cloud_percentage,
                    field_coverage_percentage=field_coverage,
                    shadow_percentage=shadow_percentage,
                    obscured_percentage=obscured_percentage,
                    threshold=threshold,
                    visualization=visualization,
                )
            )

        return options, raster_failure_count

    def _mask_source(self, scene: ProviderSceneRecord, raster: RasterOutputRecord):
        metadata_path = raster.metadata.get("mask_object_path")
        if isinstance(metadata_path, str) and metadata_path:
            return self._object_store.raster_source(metadata_path)
        if self._asset_repository is None or not scene.id:
            return None
        preferred_key = "mask" if scene.source_id in RESOURCESAT_PROFILES else "scl"
        for asset in self._asset_repository.list_for_scene(scene.id):
            if asset.asset_key != preferred_key:
                continue
            object_path = asset.mirror_object_path or asset.object_path
            if object_path:
                return self._object_store.raster_source(object_path)
        return None

    def _validate_source_index(self, source_id: str, index_name: str) -> None:
        if source_id == self._settings.sentinel2_preload_source_id:
            if index_name not in SENTINEL2_INDEX_ASSETS:
                raise ValueError(
                    f"unsupported index for {source_id}: {index_name}"
                )
            return
        profile = RESOURCESAT_PROFILES.get(source_id)
        if profile is None:
            raise ValueError(f"unsupported source_id: {source_id}")
        if not profile.supports_index(index_name):
            raise ValueError(f"unsupported index for {source_id}: {index_name}")

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
        try:
            return render_clipped_index_overlay(
                self._object_store.raster_source(raster.object_path),
                geometry=record.field_geometry,
                index_name=record.index_name,
                scale_factor=raster.scale_factor,
                nodata=raster.nodata_value,
            )
        except ObjectStoreNotFoundError as exc:
            raise AnalyticsRasterNotFound("Raster output was not found.") from exc
        except (ObjectStoreReadError, RasterioError) as exc:
            raise AnalyticsRasterUnavailable("Raster output is temporarily unavailable.") from exc

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

        try:
            value, masked, mask_class = _sample_point(
                self._object_store.raster_source(raster.object_path),
                geometry=record.field_geometry,
                lng=lng,
                lat=lat,
                scale_factor=raster.scale_factor,
                nodata=raster.nodata_value,
            )
        except ObjectStoreNotFoundError as exc:
            raise AnalyticsRasterNotFound("Raster output was not found.") from exc
        except (ObjectStoreReadError, RasterioError) as exc:
            raise AnalyticsRasterUnavailable("Raster output is temporarily unavailable.") from exc
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
            searchedSources=[request.sourceId],
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


def _field_index_window_days(source_id: str, settings: Settings) -> int:
    if source_id == settings.resourcesat_liss3_preload_source_id:
        return settings.resourcesat_liss3_preload_date_window_days
    if source_id == settings.resourcesat_liss4_preload_source_id:
        return settings.resourcesat_liss4_preload_date_window_days
    if source_id == settings.resourcesat_awifs_preload_source_id:
        return settings.resourcesat_awifs_preload_date_window_days
    return 7


def _candidate_prefilter_key(
    scene: ProviderSceneRecord,
    source_id: str,
    requested_date: object,
) -> tuple[int, float, float, float, str]:
    return (
        _date_distance_days(scene, requested_date),
        float(scene.cloud_percent if scene.cloud_percent is not None else 101.0),
        -float(scene.coverage_percentage if scene.coverage_percentage is not None else 0.0),
        _profile_native_resolution(source_id),
        scene.provider_product_id,
    )


def _candidate_quality_key(
    option: _FieldIndexCandidate,
    requested_date: object,
) -> tuple[int, float, float, float, float, str]:
    scene = option.scene
    raster = option.raster
    resolution = raster.native_resolution or raster.processing_resolution or scene.native_resolution
    fallback_resolution = _profile_native_resolution(scene.source_id)
    return (
        _date_distance_days(scene, requested_date),
        -option.usable_pixel_percentage,
        -option.field_coverage_percentage,
        option.obscured_percentage,
        float(resolution if resolution is not None else fallback_resolution),
        scene.provider_product_id,
    )


def _date_distance_days(scene: ProviderSceneRecord, requested_date: object) -> int:
    if scene.acquisition_at is None or not hasattr(requested_date, "toordinal"):
        return 9999
    return abs((scene.acquisition_at.date() - requested_date).days)


def _mask_class_policy(source_id: str) -> dict[str, tuple[int, ...]]:
    if source_id in RESOURCESAT_PROFILES:
        return {
            "nodata_classes": (0,),
            "usable_classes": (1, 4),
            "cloud_classes": (2,),
            "shadow_classes": (3,),
        }
    return {
        "nodata_classes": (0,),
        "usable_classes": (4, 5, 6),
        "cloud_classes": (8, 9, 10),
        "shadow_classes": (3,),
    }


def _provider_route_for_scene(scene: ProviderSceneRecord) -> str:
    provider_route = scene.provider_metadata.get("provider_route")
    if isinstance(provider_route, str) and provider_route:
        return provider_route
    profile = RESOURCESAT_PROFILES.get(scene.source_id)
    if profile is not None:
        return f"bhoonidhi:{profile.collection_id}"
    return "earthsearch:sentinel-2-l2a"


def _quality_warnings(source_id: str) -> list[str]:
    profile = RESOURCESAT_PROFILES.get(source_id)
    if profile is None:
        return []
    warnings: list[str] = []
    if profile.instrument == "AWiFS":
        warnings.append("AWiFS native resolution is coarse for field-scale analytics.")
    return warnings


def _atmospheric_correction_version(source_id: str) -> str:
    profile = RESOURCESAT_PROFILES.get(source_id)
    if profile is None:
        return "vendor-l2a"
    return f"bhoonidhi-{profile.analysis_level.lower()}"


def _default_cloud_mask(source_id: str) -> str:
    return RESOURCESAT_MASK_METHOD if source_id in RESOURCESAT_PROFILES else "scl-v1"


def _default_formula_version(source_id: str, index_name: str) -> str:
    if source_id in RESOURCESAT_PROFILES:
        return RESOURCESAT_FORMULA_VERSION.get(index_name, "")
    return SENTINEL2_FORMULA_VERSION.get(index_name, "")


def _native_resolution(
    scene: ProviderSceneRecord,
    raster: RasterOutputRecord,
    source_id: str,
) -> float:
    value = raster.native_resolution or raster.processing_resolution or scene.native_resolution
    return float(value if value is not None else _profile_native_resolution(source_id))


def _profile_native_resolution(source_id: str) -> float:
    profile = RESOURCESAT_PROFILES.get(source_id)
    return profile.native_resolution_m if profile is not None else 10.0


_GEOD = Geod(ellps="WGS84")


def _sample_point(
    source: RasterSource,
    *,
    geometry: dict,
    lng: float,
    lat: float,
    scale_factor: float | None,
    nodata: int | float | None,
) -> tuple[float | None, bool, int | None]:
    import numpy as np
    from pyproj import CRS, Transformer

    field_geometry = shape(geometry)
    point = Point(lng, lat)
    if field_geometry.is_empty or not field_geometry.covers(point):
        return None, True, None

    with open_raster(source) as dataset:
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
