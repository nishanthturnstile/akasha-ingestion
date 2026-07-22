"""Add standard/contrast visualization profile kinds.

Revision ID: 0005_m3_render_profiles
Revises: 0004_route_signed_https
"""

from alembic import op

revision = "0005_m3_render_profiles"
down_revision = "0004_route_signed_https"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE akasha.visualization_profiles "
        "ADD COLUMN render_profile text NOT NULL DEFAULT 'standard'"
    )
    op.execute(
        "ALTER TABLE akasha.visualization_profiles "
        'ADD COLUMN algorithm_config jsonb NOT NULL DEFAULT \'{"kind":"fixed-domain"}\'::jsonb'
    )
    op.execute(
        "ALTER TABLE akasha.visualization_profiles ADD CONSTRAINT "
        "ck_visualization_profiles_render_profile "
        "CHECK (render_profile IN ('standard', 'contrast'))"
    )
    op.execute("DROP INDEX IF EXISTS akasha.uq_visualization_profiles_default")
    op.execute(
        "CREATE UNIQUE INDEX uq_visualization_profiles_default "
        "ON akasha.visualization_profiles (index_name, render_profile) WHERE is_default"
    )
    op.execute(
        "INSERT INTO akasha.visualization_profiles "
        "(index_name, value_domain_min, value_domain_max, display_min, display_max, "
        " palette_json, nodata_color, version, is_default, render_profile, algorithm_config) "
        "SELECT index_name, value_domain_min, value_domain_max, display_min, display_max, "
        '\'["#6e3b1f","#b86b2c","#e7c64b","#9bcf53","#3f9f4a","#0b5d37"]\'::jsonb, '
        "nodata_color, 'equal-bands-v1', true, 'contrast', "
        '\'{"kind":"scene-min-max-equal-bands","breakCount":5}\'::jsonb '
        "FROM akasha.visualization_profiles WHERE is_default "
        "AND lower(index_name) IN ('ndvi','ndmi','ndwi_green_nir','msavi','ndbi','ndre','reci') "
        "ON CONFLICT (index_name, version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM akasha.visualization_profiles WHERE render_profile = 'contrast'")
    op.execute("DROP INDEX IF EXISTS akasha.uq_visualization_profiles_default")
    op.execute(
        "CREATE UNIQUE INDEX uq_visualization_profiles_default "
        "ON akasha.visualization_profiles (index_name) WHERE is_default"
    )
    op.execute(
        "ALTER TABLE akasha.visualization_profiles "
        "DROP CONSTRAINT IF EXISTS ck_visualization_profiles_render_profile"
    )
    op.execute("ALTER TABLE akasha.visualization_profiles DROP COLUMN IF EXISTS algorithm_config")
    op.execute("ALTER TABLE akasha.visualization_profiles DROP COLUMN IF EXISTS render_profile")
