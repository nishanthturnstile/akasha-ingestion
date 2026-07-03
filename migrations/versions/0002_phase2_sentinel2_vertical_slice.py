"""phase 2 sentinel 2 vertical slice foundation

Revision ID: 0002_phase2_s2_slice
Revises: 0001_core_platform
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op

revision = "0002_phase2_s2_slice"
down_revision = "0001_core_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE akasha.source_provider_routes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id text NOT NULL REFERENCES akasha.satellite_sources(source_id),
            provider_adapter text NOT NULL,
            provider_collection text NOT NULL,
            provider_priority integer NOT NULL,
            provider_role text NOT NULL,
            status text NOT NULL DEFAULT 'inactive',
            access_mode text NOT NULL,
            execution_policy_ref text REFERENCES akasha.provider_execution_policies(policy_key),
            license_profile text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_source_provider_routes_identity UNIQUE (
                source_id,
                provider_adapter,
                provider_collection
            ),
            CONSTRAINT ck_source_provider_routes_role CHECK (
                provider_role IN ('primary', 'secondary', 'fallback', 'future')
            ),
            CONSTRAINT ck_source_provider_routes_status CHECK (
                status IN ('inactive', 'manual_only', 'active', 'blocked', 'deprecated')
            ),
            CONSTRAINT ck_source_provider_routes_access CHECK (
                access_mode IN (
                    'public_https',
                    'requester_pays_s3',
                    'official_api',
                    'authenticated_download'
                )
            ),
            CONSTRAINT ck_source_provider_routes_priority CHECK (provider_priority > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_source_provider_routes_source_status
        ON akasha.source_provider_routes (source_id, status, provider_priority)
        """
    )

    op.execute(
        """
        ALTER TABLE akasha.processing_jobs
        ADD COLUMN parent_job_id uuid REFERENCES akasha.processing_jobs(id) ON DELETE SET NULL,
        ADD COLUMN scene_id uuid REFERENCES akasha.provider_scenes(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_processing_jobs_parent_status
        ON akasha.processing_jobs (parent_job_id, status)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_processing_jobs_scene_status
        ON akasha.processing_jobs (scene_id, status)
        """
    )

    op.execute(
        """
        ALTER TABLE akasha.provider_scenes
        ADD COLUMN aoi_id text REFERENCES akasha.aoi_registry(aoi_id) ON DELETE SET NULL,
        ADD COLUMN provider_route_id uuid
            REFERENCES akasha.source_provider_routes(id) ON DELETE SET NULL,
        ADD COLUMN logical_scene_key text,
        ADD COLUMN native_crs text,
        ADD COLUMN native_resolution numeric,
        ADD COLUMN coverage_percentage numeric,
        ADD COLUMN file_size_bytes bigint,
        ADD COLUMN raw_object_path text,
        ADD CONSTRAINT ck_provider_scenes_coverage CHECK (
            coverage_percentage IS NULL
            OR (coverage_percentage >= 0 AND coverage_percentage <= 100)
        ),
        ADD CONSTRAINT ck_provider_scenes_file_size CHECK (
            file_size_bytes IS NULL OR file_size_bytes >= 0
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_scenes_route_status
        ON akasha.provider_scenes (provider_route_id, status)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_scenes_aoi_acquisition
        ON akasha.provider_scenes (aoi_id, acquisition_at)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_provider_scenes_route_logical_key
        ON akasha.provider_scenes (provider_route_id, logical_scene_key)
        WHERE logical_scene_key IS NOT NULL
        """
    )

    op.execute("ALTER TABLE akasha.scene_assets ALTER COLUMN object_path DROP NOT NULL")
    op.execute(
        """
        ALTER TABLE akasha.scene_assets
        ADD COLUMN asset_href text,
        ADD COLUMN storage_backend text NOT NULL DEFAULT 'minio',
        ADD COLUMN storage_region text,
        ADD COLUMN requester_pays boolean NOT NULL DEFAULT false,
        ADD COLUMN asset_key text,
        ADD COLUMN scale numeric,
        ADD COLUMN offset_value numeric,
        ADD COLUMN nodata_value numeric,
        ADD COLUMN roles text[],
        ADD COLUMN media_type text,
        ADD COLUMN mirror_status text NOT NULL DEFAULT 'not_required',
        ADD COLUMN mirror_object_path text,
        ADD COLUMN mirror_checksum_sha256 text,
        ADD COLUMN selected_access_mode text,
        ADD CONSTRAINT ck_scene_assets_location CHECK (
            object_path IS NOT NULL OR asset_href IS NOT NULL OR mirror_object_path IS NOT NULL
        ),
        ADD CONSTRAINT ck_scene_assets_storage_backend CHECK (
            storage_backend IN ('minio', 'https', 's3', 'local')
        ),
        ADD CONSTRAINT ck_scene_assets_mirror_status CHECK (
            mirror_status IN (
                'not_required',
                'pending',
                'mirroring',
                'mirrored',
                'failed',
                'skipped'
            )
        ),
        ADD CONSTRAINT ck_scene_assets_requester_pays_access CHECK (
            requester_pays = false OR selected_access_mode = 'requester_pays_s3'
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_scene_assets_asset_key
        ON akasha.scene_assets (scene_id, asset_key)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_scene_assets_scene_asset_key
        ON akasha.scene_assets (scene_id, asset_key)
        WHERE asset_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_scene_assets_mirror_status
        ON akasha.scene_assets (mirror_status, scene_id)
        """
    )

    op.execute(
        """
        ALTER TABLE akasha.raster_outputs
        ADD COLUMN dtype text,
        ADD COLUMN scale_factor numeric,
        ADD COLUMN offset_value numeric,
        ADD COLUMN nodata_value numeric,
        ADD COLUMN min_value numeric,
        ADD COLUMN max_value numeric,
        ADD COLUMN native_resolution numeric,
        ADD COLUMN processing_resolution numeric,
        ADD COLUMN display_resolution numeric,
        ADD COLUMN crs text,
        ADD COLUMN cloud_mask_version text,
        ADD CONSTRAINT ck_raster_outputs_derived_identity CHECK (
            output_kind <> 'derived_index'
            OR (
                scene_id IS NOT NULL
                AND index_name IS NOT NULL
                AND formula_version IS NOT NULL
                AND processing_profile_version IS NOT NULL
                AND processing_resolution IS NOT NULL
            )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_raster_outputs_derived_identity
        ON akasha.raster_outputs (
            scene_id,
            output_kind,
            index_name,
            formula_version,
            processing_profile_version,
            processing_resolution
        )
        WHERE output_kind = 'derived_index'
        """
    )

    op.execute(
        """
        ALTER TABLE akasha.tile_layers
        ADD CONSTRAINT ck_tile_layers_visibility CHECK (
            visibility IN ('internal', 'private', 'public')
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.processing_job_stages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id uuid NOT NULL REFERENCES akasha.processing_jobs(id) ON DELETE CASCADE,
            stage_name text NOT NULL,
            attempt integer NOT NULL CHECK (attempt > 0),
            status text NOT NULL DEFAULT 'pending',
            error_code text,
            error_message text,
            lease_owner text,
            lease_expires_at timestamptz,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            started_at timestamptz,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_processing_job_stages_attempt UNIQUE (job_id, stage_name, attempt),
            CONSTRAINT ck_processing_job_stages_status CHECK (
                status IN (
                    'pending',
                    'running',
                    'completed',
                    'failed',
                    'skipped',
                    'retrying',
                    'cancelled'
                )
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_processing_job_stages_job_stage_status
        ON akasha.processing_job_stages (job_id, stage_name, status)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_processing_job_stages_status_lease
        ON akasha.processing_job_stages (status, lease_expires_at)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_processing_job_stages_running
        ON akasha.processing_job_stages (job_id, stage_name)
        WHERE status = 'running'
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.backfill_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id uuid NOT NULL REFERENCES akasha.processing_jobs(id) ON DELETE CASCADE,
            source_id text NOT NULL,
            aoi_id text NOT NULL,
            date_start date NOT NULL,
            date_end date NOT NULL,
            status text NOT NULL DEFAULT 'running',
            searched_count integer NOT NULL DEFAULT 0,
            accepted_count integer NOT NULL DEFAULT 0,
            mirrored_asset_count integer NOT NULL DEFAULT 0,
            skipped_count integer NOT NULL DEFAULT 0,
            processed_count integer NOT NULL DEFAULT 0,
            failed_count integer NOT NULL DEFAULT 0,
            retryable_failed_count integer NOT NULL DEFAULT 0,
            terminal_failed_count integer NOT NULL DEFAULT 0,
            estimated_source_mirror_bytes bigint,
            actual_source_mirror_bytes bigint,
            summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            started_at timestamptz,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_backfill_runs_job_scope UNIQUE (
                source_id,
                aoi_id,
                date_start,
                date_end,
                job_id
            ),
            CONSTRAINT ck_backfill_runs_status CHECK (
                status IN ('running', 'completed', 'failed', 'partial')
            ),
            CONSTRAINT ck_backfill_runs_date_range CHECK (date_end >= date_start),
            CONSTRAINT ck_backfill_runs_counts CHECK (
                searched_count >= 0
                AND accepted_count >= 0
                AND mirrored_asset_count >= 0
                AND skipped_count >= 0
                AND processed_count >= 0
                AND failed_count >= 0
                AND retryable_failed_count >= 0
                AND terminal_failed_count >= 0
            ),
            CONSTRAINT ck_backfill_runs_mirror_bytes CHECK (
                (estimated_source_mirror_bytes IS NULL OR estimated_source_mirror_bytes >= 0)
                AND (actual_source_mirror_bytes IS NULL OR actual_source_mirror_bytes >= 0)
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_backfill_runs_scope_status
        ON akasha.backfill_runs (source_id, aoi_id, date_start, date_end, status)
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.visualization_profiles (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            index_name text NOT NULL,
            value_domain_min numeric NOT NULL,
            value_domain_max numeric NOT NULL,
            display_min numeric NOT NULL,
            display_max numeric NOT NULL,
            palette_json jsonb NOT NULL,
            nodata_color text NOT NULL DEFAULT 'transparent',
            version text NOT NULL,
            is_default boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_visualization_profiles_index_version UNIQUE (index_name, version),
            CONSTRAINT ck_visualization_profiles_domains CHECK (
                value_domain_max > value_domain_min AND display_max > display_min
            )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visualization_profiles_default
        ON akasha.visualization_profiles (index_name)
        WHERE is_default
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.threshold_profiles (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            profile_key text NOT NULL,
            index_name text NOT NULL,
            crop text,
            season text,
            aoi_id text,
            source_id text,
            classes_json jsonb NOT NULL,
            is_default boolean NOT NULL DEFAULT false,
            version text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_threshold_profiles_key UNIQUE (profile_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_threshold_profiles_lookup
        ON akasha.threshold_profiles (
            index_name,
            crop,
            season,
            aoi_id,
            source_id,
            is_default
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.field_queries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            query_id text NOT NULL UNIQUE,
            field_geometry geometry(Geometry, 4326) NOT NULL,
            crs text NOT NULL DEFAULT 'EPSG:4326',
            index_name text NOT NULL,
            requested_date date NOT NULL,
            selected_scene_id uuid REFERENCES akasha.provider_scenes(id) ON DELETE SET NULL,
            raster_output_id uuid REFERENCES akasha.raster_outputs(id) ON DELETE SET NULL,
            layer_id text REFERENCES akasha.tile_layers(layer_id) ON DELETE SET NULL,
            valid_pixel_count integer NOT NULL DEFAULT 0,
            selection_reason text NOT NULL,
            stats_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            class_area_json jsonb NOT NULL DEFAULT '[]'::jsonb,
            quality_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            visualization_profile_id uuid
                REFERENCES akasha.visualization_profiles(id) ON DELETE SET NULL,
            threshold_profile_id uuid REFERENCES akasha.threshold_profiles(id) ON DELETE SET NULL,
            expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_field_queries_crs CHECK (crs = 'EPSG:4326'),
            CONSTRAINT ck_field_queries_valid_pixels CHECK (valid_pixel_count >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_field_queries_geometry
        ON akasha.field_queries USING GIST (field_geometry)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_field_queries_index_date_created
        ON akasha.field_queries (index_name, requested_date, created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS akasha.field_queries")
    op.execute("DROP TABLE IF EXISTS akasha.threshold_profiles")
    op.execute("DROP TABLE IF EXISTS akasha.visualization_profiles")
    op.execute("DROP TABLE IF EXISTS akasha.backfill_runs")
    op.execute("DROP TABLE IF EXISTS akasha.processing_job_stages")

    op.execute("ALTER TABLE akasha.tile_layers DROP CONSTRAINT IF EXISTS ck_tile_layers_visibility")

    op.execute("DROP INDEX IF EXISTS akasha.uq_raster_outputs_derived_identity")
    op.execute(
        """
        ALTER TABLE akasha.raster_outputs
        DROP CONSTRAINT IF EXISTS ck_raster_outputs_derived_identity,
        DROP COLUMN IF EXISTS cloud_mask_version,
        DROP COLUMN IF EXISTS crs,
        DROP COLUMN IF EXISTS display_resolution,
        DROP COLUMN IF EXISTS processing_resolution,
        DROP COLUMN IF EXISTS native_resolution,
        DROP COLUMN IF EXISTS max_value,
        DROP COLUMN IF EXISTS min_value,
        DROP COLUMN IF EXISTS nodata_value,
        DROP COLUMN IF EXISTS offset_value,
        DROP COLUMN IF EXISTS scale_factor,
        DROP COLUMN IF EXISTS dtype
        """
    )

    op.execute("DROP INDEX IF EXISTS akasha.ix_scene_assets_mirror_status")
    op.execute("DROP INDEX IF EXISTS akasha.uq_scene_assets_scene_asset_key")
    op.execute("DROP INDEX IF EXISTS akasha.ix_scene_assets_asset_key")
    op.execute(
        """
        ALTER TABLE akasha.scene_assets
        DROP CONSTRAINT IF EXISTS ck_scene_assets_requester_pays_access,
        DROP CONSTRAINT IF EXISTS ck_scene_assets_mirror_status,
        DROP CONSTRAINT IF EXISTS ck_scene_assets_storage_backend,
        DROP CONSTRAINT IF EXISTS ck_scene_assets_location,
        DROP COLUMN IF EXISTS selected_access_mode,
        DROP COLUMN IF EXISTS mirror_checksum_sha256,
        DROP COLUMN IF EXISTS mirror_object_path,
        DROP COLUMN IF EXISTS mirror_status,
        DROP COLUMN IF EXISTS media_type,
        DROP COLUMN IF EXISTS roles,
        DROP COLUMN IF EXISTS nodata_value,
        DROP COLUMN IF EXISTS offset_value,
        DROP COLUMN IF EXISTS scale,
        DROP COLUMN IF EXISTS asset_key,
        DROP COLUMN IF EXISTS requester_pays,
        DROP COLUMN IF EXISTS storage_region,
        DROP COLUMN IF EXISTS storage_backend,
        DROP COLUMN IF EXISTS asset_href
        """
    )
    op.execute(
        """
        UPDATE akasha.scene_assets
        SET object_path = 'downgrade-missing-object-path'
        WHERE object_path IS NULL
        """
    )
    op.execute("ALTER TABLE akasha.scene_assets ALTER COLUMN object_path SET NOT NULL")

    op.execute("DROP INDEX IF EXISTS akasha.uq_provider_scenes_route_logical_key")
    op.execute("DROP INDEX IF EXISTS akasha.ix_provider_scenes_aoi_acquisition")
    op.execute("DROP INDEX IF EXISTS akasha.ix_provider_scenes_route_status")
    op.execute(
        """
        ALTER TABLE akasha.provider_scenes
        DROP CONSTRAINT IF EXISTS ck_provider_scenes_file_size,
        DROP CONSTRAINT IF EXISTS ck_provider_scenes_coverage,
        DROP COLUMN IF EXISTS raw_object_path,
        DROP COLUMN IF EXISTS file_size_bytes,
        DROP COLUMN IF EXISTS coverage_percentage,
        DROP COLUMN IF EXISTS native_resolution,
        DROP COLUMN IF EXISTS native_crs,
        DROP COLUMN IF EXISTS logical_scene_key,
        DROP COLUMN IF EXISTS provider_route_id,
        DROP COLUMN IF EXISTS aoi_id
        """
    )

    op.execute("DROP INDEX IF EXISTS akasha.ix_processing_jobs_scene_status")
    op.execute("DROP INDEX IF EXISTS akasha.ix_processing_jobs_parent_status")
    op.execute(
        """
        ALTER TABLE akasha.processing_jobs
        DROP COLUMN IF EXISTS scene_id,
        DROP COLUMN IF EXISTS parent_job_id
        """
    )

    op.execute("DROP TABLE IF EXISTS akasha.source_provider_routes")
