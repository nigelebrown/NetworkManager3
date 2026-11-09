"""Phase 3 read-only configuration inheritance templates (management DB)."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, utcnow
from .models_inm import new_uuid


class InmConfigTemplate(Base):
    __tablename__ = "inm_config_templates"
    __table_args__ = (UniqueConstraint("scope", "name", name="uq_inm_template_scope_name"),
                      Index("ix_inm_template_site", "site_id"), Index("ix_inm_template_device", "device_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(12), nullable=False)  # global/site/device
    site_id: Mapped[str | None] = mapped_column(ForeignKey("inm_sites.id"), nullable=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("inm_devices.id"), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)   # 0010: group scope
    model_contains: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
