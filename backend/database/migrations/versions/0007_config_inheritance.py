"""Phase 3 config inheritance (expand only).

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("inm_config_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(12), nullable=False),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("inm_sites.id")),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("inm_devices.id")),
        sa.Column("model_contains", sa.String(64)),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "name", name="uq_inm_template_scope_name"),
    )
    op.create_index("ix_inm_template_site", "inm_config_templates", ["site_id"])
    op.create_index("ix_inm_template_device", "inm_config_templates", ["device_id"])

def downgrade():
    raise RuntimeError("Phase 3 migration is expand-only; restore the encrypted backup.")
