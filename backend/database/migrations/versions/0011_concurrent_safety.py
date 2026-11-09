"""Atomic notification-delivery claim state for safe parallel workers.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("notification_deliveries") as batch:
        batch.add_column(sa.Column("claim_owner", sa.String(64), nullable=True))
        batch.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_notification_deliveries_claim_expires_at", ["claim_expires_at"])


def downgrade():
    with op.batch_alter_table("notification_deliveries") as batch:
        batch.drop_index("ix_notification_deliveries_claim_expires_at")
        batch.drop_column("claim_expires_at")
        batch.drop_column("claim_owner")
