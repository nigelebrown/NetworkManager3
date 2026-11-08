"""Reusable credential library and read-only onboarding workflow (expand-only).

Revision ID: 0006
Revises: 0005
Existing inventory, printers, telemetry and encrypted credentials remain intact.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("onboarding_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("device_family", sa.String(32), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_status", sa.String(16)),
    )
    op.create_table("onboarding_discoveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("credential_id", sa.String(36), sa.ForeignKey("onboarding_credentials.id"), nullable=False),
        sa.Column("site_name", sa.String(100), nullable=False),
        sa.Column("scope", sa.String(48), nullable=False),
        sa.Column("device_family", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_onboarding_discoveries_credential_id", "onboarding_discoveries", ["credential_id"])
    op.create_table("onboarding_config_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("inm_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_by", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("completeness", sa.String(24), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("warning", sa.String(400)),
    )
    op.create_index("ix_onboarding_config_snapshots_device_id", "onboarding_config_snapshots", ["device_id"])
    op.create_table("onboarding_config_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("inm_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baseline_id", sa.String(36), sa.ForeignKey("onboarding_config_snapshots.id")),
        sa.Column("proposed_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_onboarding_config_drafts_device_id", "onboarding_config_drafts", ["device_id"])

def downgrade() -> None:
    raise RuntimeError("Migration 0006 is expand-only. Restore a tested encrypted pre-upgrade backup.")
