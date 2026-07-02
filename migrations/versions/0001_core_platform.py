"""core platform schema

Revision ID: 0001_core_platform
Revises:
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op

revision = "0001_core_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SCHEMA IF NOT EXISTS akasha")

    op.execute(
        """
        CREATE TABLE akasha.satellite_sources (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id text NOT NULL UNIQUE,
            catalog_slug text NOT NULL,
            provider_adapter text NOT NULL,
            instrument_mode text NOT NULL,
            analysis_level text NOT NULL,
            bands jsonb NOT NULL DEFAULT '[]'::jsonb,
            supported_indices jsonb NOT NULL DEFAULT '[]'::jsonb,
            schedule_state text NOT NULL DEFAULT 'disabled',
            product_exposure text NOT NULL DEFAULT 'hidden',
            status text NOT NULL DEFAULT 'disabled',
            credential_ref text,
            execution_policy_ref text,
            validation_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
            processing_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
            license_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
            provider_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.source_credentials (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_adapter text NOT NULL,
            secret_ref text NOT NULL UNIQUE,
            status text NOT NULL DEFAULT 'pending',
            rotated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.provider_execution_policies (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_key text NOT NULL UNIQUE,
            provider_adapter text NOT NULL,
            source_id text,
            auth_model text NOT NULL,
            requests_per_minute integer NOT NULL DEFAULT 60 CHECK (requests_per_minute > 0),
            max_concurrent_searches integer NOT NULL DEFAULT 1 CHECK (max_concurrent_searches > 0),
            max_concurrent_downloads integer NOT NULL DEFAULT 1
                CHECK (max_concurrent_downloads > 0),
            daily_quota integer,
            retry_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            staging_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            checksum_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            availability_lag_hours integer NOT NULL DEFAULT 0,
            priority_class text NOT NULL DEFAULT 'routine',
            enabled boolean NOT NULL DEFAULT false,
            version text NOT NULL DEFAULT 'v1',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.aoi_registry (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            aoi_id text NOT NULL UNIQUE,
            name text NOT NULL,
            geometry geometry(Geometry, 4326) NOT NULL,
            bbox jsonb NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.provider_scenes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_adapter text NOT NULL,
            source_id text NOT NULL,
            provider_product_id text NOT NULL,
            acquisition_at timestamptz,
            scene_geometry geometry(Geometry, 4326),
            status text NOT NULL DEFAULT 'discovered',
            cloud_percent numeric,
            license_state text NOT NULL DEFAULT 'unknown',
            pgstac_item_id text,
            provider_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (provider_adapter, provider_product_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.provider_orders (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            scene_id uuid REFERENCES akasha.provider_scenes(id) ON DELETE CASCADE,
            provider_order_id text,
            status text NOT NULL DEFAULT 'not_required',
            download_url_expires_at timestamptz,
            order_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.scene_assets (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            scene_id uuid REFERENCES akasha.provider_scenes(id) ON DELETE CASCADE,
            asset_kind text NOT NULL,
            band_role text,
            object_path text NOT NULL,
            checksum_sha256 text,
            size_bytes bigint,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.processing_jobs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            job_type text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            idempotency_key text NOT NULL,
            source_id text,
            aoi_id text,
            execution_policy_version text,
            request_params jsonb NOT NULL DEFAULT '{}'::jsonb,
            result_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            error_code text,
            error_message text,
            queued_at timestamptz,
            started_at timestamptz,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.raster_outputs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            scene_id uuid REFERENCES akasha.provider_scenes(id) ON DELETE SET NULL,
            output_kind text NOT NULL,
            index_name text,
            object_path text NOT NULL,
            checksum_sha256 text,
            formula_version text,
            processing_profile_version text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.tile_layers (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            layer_id text NOT NULL UNIQUE,
            raster_output_id uuid REFERENCES akasha.raster_outputs(id) ON DELETE CASCADE,
            visibility text NOT NULL DEFAULT 'internal',
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE akasha.audit_logs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            actor text,
            event_type text NOT NULL,
            entity_type text NOT NULL,
            entity_id text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_satellite_sources_provider
        ON akasha.satellite_sources (provider_adapter)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_execution_policies_provider
        ON akasha.provider_execution_policies (provider_adapter, source_id, enabled)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_aoi_registry_geometry
        ON akasha.aoi_registry USING GIST (geometry)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_scenes_geometry
        ON akasha.provider_scenes USING GIST (scene_geometry)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_scenes_source_status
        ON akasha.provider_scenes (source_id, status)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_scene_assets_scene_kind
        ON akasha.scene_assets (scene_id, asset_kind, band_role)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_processing_jobs_status_type_created
        ON akasha.processing_jobs (status, job_type, created_at)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_processing_jobs_active_idempotency
        ON akasha.processing_jobs (idempotency_key)
        WHERE status IN ('pending', 'queued', 'running', 'completed')
        """
    )
    op.execute(
        """
        CREATE INDEX ix_raster_outputs_scene_index
        ON akasha.raster_outputs (scene_id, index_name)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_logs_created_event
        ON akasha.audit_logs (created_at, event_type)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS akasha.audit_logs")
    op.execute("DROP TABLE IF EXISTS akasha.tile_layers")
    op.execute("DROP TABLE IF EXISTS akasha.raster_outputs")
    op.execute("DROP TABLE IF EXISTS akasha.processing_jobs")
    op.execute("DROP TABLE IF EXISTS akasha.scene_assets")
    op.execute("DROP TABLE IF EXISTS akasha.provider_orders")
    op.execute("DROP TABLE IF EXISTS akasha.provider_scenes")
    op.execute("DROP TABLE IF EXISTS akasha.aoi_registry")
    op.execute("DROP TABLE IF EXISTS akasha.provider_execution_policies")
    op.execute("DROP TABLE IF EXISTS akasha.source_credentials")
    op.execute("DROP TABLE IF EXISTS akasha.satellite_sources")
