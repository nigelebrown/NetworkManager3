"""Printer Intelligence: additive inventory, meters, supplies, lifecycle and maintenance.

Revision ID: 0005
Revises: 0004
Expand-only: no legacy tables or columns are removed.
Existing data in 0004 remain untouched. Do not downgrade automatically.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("printer_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("manufacturer", sa.String(64), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("serial_number", sa.String(100), nullable=False),
        sa.Column("hostname", sa.String(120)),
        sa.Column("management_ip", sa.String(45)),
        sa.Column("firmware", sa.String(80)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("provenance", sa.String(24), nullable=False),
        sa.Column("area", sa.String(100)),
        sa.Column("division", sa.String(100)),
        sa.Column("branch", sa.String(100)),
        sa.Column("station", sa.String(100)),
        sa.Column("section_unit", sa.String(100)),
        sa.Column("building", sa.String(100)),
        sa.Column("capabilities", sa.JSON()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manufacturer", "serial_number", name="uq_printer_manufacturer_serial"),
    )
    op.create_table("printer_meter_readings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.String(36), sa.ForeignKey("printer_devices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("meter_kind", sa.String(40), nullable=False),
        sa.Column("counter_value", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("quality", sa.String(24), nullable=False),
        sa.Column("note", sa.String(255)),
        sa.UniqueConstraint("printer_id", "meter_kind", "observed_at", name="uq_printer_meter_time"),
    )
    op.create_table("printer_supply_readings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.String(36), sa.ForeignKey("printer_devices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("supply_name", sa.String(100), nullable=False),
        sa.Column("colour", sa.String(32), nullable=False),
        sa.Column("percent_remaining", sa.Numeric(6, 2)),
        sa.Column("reported_remaining", sa.Integer()),
        sa.Column("reported_capacity", sa.Integer()),
        sa.Column("reported_unit", sa.String(30)),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("printer_consumable_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("printer_id", sa.String(36), sa.ForeignKey("printer_devices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("consumable_kind", sa.String(40), nullable=False),
        sa.Column("colour", sa.String(32), nullable=False),
        sa.Column("part_number", sa.String(100)),
        sa.Column("supply_brand", sa.String(80)),
        sa.Column("quality_class", sa.String(24), nullable=False),
        sa.Column("supplier", sa.String(100)),
        sa.Column("meter_kind", sa.String(40), nullable=False),
        sa.Column("start_meter", sa.BigInteger(), nullable=False),
        sa.Column("end_meter", sa.BigInteger()),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("partial_refill", sa.Boolean(), nullable=False),
        sa.Column("quantity_added_ml", sa.Numeric(12, 3)),
        sa.Column("measured_consumed_ml", sa.Numeric(12, 3)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cost_amount", sa.Numeric(12, 2)),
        sa.Column("declared_yield", sa.Integer()),
        sa.Column("notes", sa.Text()),
    )
    op.create_table("printer_maintenance_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.String(36), sa.ForeignKey("printer_devices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("component", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("fault_code", sa.String(50)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cost_amount", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("logged_by", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("0005 is expand-only. Restore a pre-upgrade, verified encrypted database backup.")
