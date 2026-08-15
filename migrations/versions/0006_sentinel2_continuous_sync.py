"""sentinel-2 continuous sync ledger and scene retry state

Revision ID: 0006_sentinel2_continuous_sync
Revises: 0005_m3_render_profiles
"""

from __future__ import annotations

from alembic import op

revision = "0006_sentinel2_continuous_sync"
down_revision = "0005_m3_render_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE akasha.sentinel2_sync_ledger (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id text NOT NULL,
            aoi_id text NOT NULL,
            provider_date date NOT NULL,
            status text NOT NULL DEFAULT 'running',
            scene_count integer NOT NULL DEFAULT 0 CHECK (scene_count >= 0),
            searched_count integer NOT NULL DEFAULT 0 CHECK (searched_count >= 0),
            processed_count integer NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
            failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
            retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            search_complete boolean NOT NULL DEFAULT false,
            last_error text,
            heartbeat_at timestamptz,
            started_at timestamptz,
            completed_at timestamptz,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_sentinel2_sync_ledger_day UNIQUE (source_id, aoi_id, provider_date),
            CONSTRAINT ck_sentinel2_sync_ledger_status CHECK (
                status IN ('running', 'complete', 'partial', 'failed', 'retry')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_sentinel2_sync_ledger_aoi_status
        ON akasha.sentinel2_sync_ledger (source_id, aoi_id, status, provider_date)
        """
    )
    op.execute(
        """
        ALTER TABLE akasha.provider_scenes
        ADD COLUMN processing_state text NOT NULL DEFAULT 'pending',
        ADD COLUMN retry_count integer NOT NULL DEFAULT 0,
        ADD COLUMN last_error text,
        ADD COLUMN last_attempt_at timestamptz,
        ADD CONSTRAINT ck_provider_scenes_processing_state CHECK (
            processing_state IN ('pending', 'processing', 'complete', 'retrying', 'failed')
        ),
        ADD CONSTRAINT ck_provider_scenes_retry_count CHECK (retry_count >= 0)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_scenes_processing_state
        ON akasha.provider_scenes (aoi_id, source_id, processing_state)
        """
    )
    op.execute(
        """
        UPDATE akasha.provider_scenes AS scene
        SET processing_state = 'complete', last_error = NULL
        WHERE scene.source_id = 'sentinel-2-l2a'
          AND scene.pgstac_item_id IS NOT NULL
          AND (
              SELECT COUNT(DISTINCT output.index_name)
              FROM akasha.raster_outputs AS output
              WHERE output.scene_id = scene.id
                AND output.output_kind = 'derived_index'
                AND output.index_name IN ('ndvi', 'msavi', 'ndmi', 'ndbi', 'ndre', 'reci')
          ) = 6
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS akasha.ix_provider_scenes_processing_state")
    op.execute(
        """
        ALTER TABLE akasha.provider_scenes
        DROP CONSTRAINT IF EXISTS ck_provider_scenes_retry_count,
        DROP CONSTRAINT IF EXISTS ck_provider_scenes_processing_state,
        DROP COLUMN IF EXISTS last_attempt_at,
        DROP COLUMN IF EXISTS last_error,
        DROP COLUMN IF EXISTS retry_count,
        DROP COLUMN IF EXISTS processing_state
        """
    )
    op.execute("DROP INDEX IF EXISTS akasha.ix_sentinel2_sync_ledger_aoi_status")
    op.execute("DROP TABLE IF EXISTS akasha.sentinel2_sync_ledger")
