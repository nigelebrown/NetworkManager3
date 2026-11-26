"""rc17: printer purchase requests, print-job metadata and print-job inspection findings (expand-only).

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "printer_purchase_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("printer_id", sa.String(36), sa.ForeignKey("printer_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supply_index", sa.String(32)), sa.Column("supply_description", sa.String(255), nullable=False),
        sa.Column("colour", sa.String(16)), sa.Column("part_number", sa.String(100)),
        sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("estimated_unit_cost", sa.Numeric(14, 2)),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False), sa.Column("origin", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=False), sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)), sa.Column("decision_note", sa.String(500)),
        sa.Column("order_reference", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_printer_purchase_requests_printer_id", "printer_purchase_requests", ["printer_id"])
    op.create_index("ix_printer_purchase_requests_created_at", "printer_purchase_requests", ["created_at"])
    op.create_index("ix_purchase_open_key", "printer_purchase_requests", ["dedupe_key", "status"])
    op.create_table(
        "print_jobs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_job_id", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("printer_id", sa.String(36), sa.ForeignKey("printer_devices.id", ondelete="SET NULL")),
        sa.Column("printer_ref", sa.String(120), nullable=False), sa.Column("user_ref", sa.String(128), nullable=False),
        sa.Column("user_pseudonymised", sa.Boolean(), nullable=False), sa.Column("document_name", sa.String(255)),
        sa.Column("pages", sa.Integer()), sa.Column("copies", sa.Integer(), nullable=False),
        sa.Column("colour", sa.Boolean()), sa.Column("duplex", sa.Boolean()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inspection_state", sa.String(16), nullable=False), sa.Column("content_sha256", sa.String(64)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_print_jobs_printer_id", "print_jobs", ["printer_id"])
    op.create_index("ix_print_jobs_completed_at", "print_jobs", ["completed_at"])
    op.create_index("ix_print_jobs_printer_time", "print_jobs", ["printer_id", "completed_at"])
    op.create_table(
        "print_job_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_name", sa.String(64), nullable=False), sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("match_count", sa.Integer(), nullable=False), sa.Column("excerpt", sa.Text()),
        sa.Column("review_state", sa.String(16), nullable=False), sa.Column("reviewed_by", sa.String(64)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_print_job_findings_job_id", "print_job_findings", ["job_id"])


def downgrade():
    raise RuntimeError("Restore the pre-upgrade backup; dropping purchasing and print-job tables would delete records.")
