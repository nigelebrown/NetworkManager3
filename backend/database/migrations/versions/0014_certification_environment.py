"""Separate hardware and emulated certification scope; preserve all evidence.

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("device_certifications") as batch:
        batch.drop_constraint("uq_cert_scope_op", type_="unique")
        batch.create_unique_constraint("uq_cert_scope_env_op",
            ["adapter_key", "model_family", "firmware_train", "operation", "environment"])


def downgrade():
    raise RuntimeError("Restore the pre-upgrade backup; a downgrade may conflate hardware and emulator certifications.")
