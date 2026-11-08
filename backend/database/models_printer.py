"""Printer Intelligence bounded domain — append-only metering and consumable cycles.

This module shares the management database, authentication and audit chain with INM,
but never treats a printer as an Aruba access point or switch. No print contents,
document titles or per-user print jobs are stored.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid


class PrinterDevice(Base):
    __tablename__ = "printer_devices"
    __table_args__ = (UniqueConstraint("manufacturer", "serial_number", name="uq_printer_manufacturer_serial"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(100), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    management_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown")
    provenance: Mapped[str] = mapped_column(String(24), default="MANUAL")  # MANUAL | SNMPV3
    area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    division: Mapped[str | None] = mapped_column(String(100), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    station: Mapped[str | None] = mapped_column(String(100), nullable=True)
    section_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    building: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterMeterReading(Base):
    __tablename__ = "printer_meter_readings"
    __table_args__ = (UniqueConstraint("printer_id", "meter_kind", "observed_at", name="uq_printer_meter_time"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    meter_kind: Mapped[str] = mapped_column(String(40))  # impressions_total, sheets_total etc.; never equated
    counter_value: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(24))  # MANUAL | SNMPV3 | IPP
    quality: Mapped[str] = mapped_column(String(24), default="REPORTED")
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PrinterSupplyReading(Base):
    __tablename__ = "printer_supply_readings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    supply_name: Mapped[str] = mapped_column(String(100))
    colour: Mapped[str] = mapped_column(String(32))  # black/cyan/magenta/yellow/other
    percent_remaining: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    reported_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str] = mapped_column(String(24))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterConsumableCycle(Base):
    __tablename__ = "printer_consumable_cycles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    consumable_kind: Mapped[str] = mapped_column(String(40))  # toner, cartridge, ink_tank, drum, etc.
    colour: Mapped[str] = mapped_column(String(32))
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supply_brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    quality_class: Mapped[str] = mapped_column(String(24), default="unknown")  # genuine/compatible/remanufactured/unknown
    supplier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    meter_kind: Mapped[str] = mapped_column(String(40), default="impressions_total")
    start_meter: Mapped[int] = mapped_column(Integer)
    end_meter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | closed
    partial_refill: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity_added_ml: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    measured_consumed_ml: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="JMD")
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    declared_yield: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PrinterMaintenanceEvent(Base):
    __tablename__ = "printer_maintenance_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    component: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(30))  # fault/inspection/service/replacement
    severity: Mapped[str] = mapped_column(String(20), default="info")
    fault_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="JMD")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(24), default="MANUAL")
    logged_by: Mapped[str] = mapped_column(String(64), default="unknown")
