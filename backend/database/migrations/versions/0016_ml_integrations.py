"""rc18: trained-model lifecycle (training runs, drift reports, scored inferences, model registry columns),
REST API tokens, encrypted per-channel integration credentials, and three columns widened after the contract
suite ran on PostgreSQL (recommendations.rule_version 64, notification_deliveries.kind 24, change_plans.source 32)
(expand-only).

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade():
    for name, col in (("target", sa.String(64)), ("version", sa.Integer()), ("dataset_source", sa.String(12)),
                      ("validation_note", sa.String(300)), ("training_profile", sa.JSON()),
                      ("health", sa.String(16)), ("health_detail", sa.JSON())):
        op.add_column("ml_models", sa.Column(name, col, nullable=True))
    op.add_column("ml_models", sa.Column("production_validated", sa.Boolean(), nullable=False,
                                         server_default=sa.false()))
    op.create_index("ix_ml_models_target", "ml_models", ["target"])
    op.create_table(
        "ml_training_runs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("target", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False), sa.Column("dataset_source", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("model_id", sa.String(36)), sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("started_at", TS, nullable=False), sa.Column("finished_at", TS))
    op.create_index("ix_ml_training_runs_target", "ml_training_runs", ["target"])
    op.create_index("ix_ml_training_runs_started_at", "ml_training_runs", ["started_at"])
    op.create_table(
        "ml_drift_reports",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("model_id", sa.String(36), nullable=False),
        sa.Column("checked_at", TS, nullable=False), sa.Column("state", sa.String(20), nullable=False),
        sa.Column("max_psi", sa.Float()), sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False))
    op.create_index("ix_ml_drift_reports_model_id", "ml_drift_reports", ["model_id"])
    op.create_index("ix_ml_drift_reports_checked_at", "ml_drift_reports", ["checked_at"])
    op.create_table(
        "ml_inferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model_id", sa.String(36), nullable=False), sa.Column("target", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(36), nullable=False), sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False), sa.Column("issued_at", TS, nullable=False),
        sa.Column("window_end", TS, nullable=False), sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("used_for_prediction", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(12)), sa.Column("observed_peak", sa.Float()), sa.Column("evaluated_at", TS))
    op.create_index("ix_ml_inferences_model_id", "ml_inferences", ["model_id"])
    op.create_index("ix_ml_inferences_device_id", "ml_inferences", ["device_id"])
    op.create_index("ix_ml_inferences_open", "ml_inferences", ["outcome", "window_end"])
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.JSON(), nullable=False), sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False), sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", TS), sa.Column("last_used_ip", sa.String(64)),
        sa.Column("revoked_at", TS), sa.Column("revoked_by", sa.String(64)))
    op.create_index("ix_api_tokens_prefix", "api_tokens", ["prefix"])
    op.add_column("notification_channels", sa.Column("config", sa.JSON(), nullable=True))
    op.add_column("notification_channels", sa.Column("secret_enc", sa.Text(), nullable=True))
    op.add_column("notification_channels", sa.Column("last_test_at", TS, nullable=True))
    op.add_column("notification_channels", sa.Column("last_test_ok", sa.Boolean(), nullable=True))
    # Defect found by running the contract suite on PostgreSQL: predictive recommendation versions such as
    # "baseline-trend-v1+v1" (20 chars) exceeded VARCHAR(16), so PostgreSQL refused every predictive
    # recommendation (SQLite does not enforce lengths). Widening is expand-only and keeps every value.
    with op.batch_alter_table("recommendations") as b:
        b.alter_column("rule_version", existing_type=sa.String(16), type_=sa.String(64), existing_nullable=False)
    # Same class of defect: scheduled printer-report notifications use kind "printer_report" (14 chars) and
    # PostgreSQL refused them as VARCHAR(12); the report job then failed. Widened, expand-only.
    with op.batch_alter_table("notification_deliveries") as b:
        b.alter_column("kind", existing_type=sa.String(12), type_=sa.String(24), existing_nullable=False)
    # ...and automatic optimizer rollback plans (source "AUTO_OPTIMIZER_ROLLBACK", 23 chars) could not be created.
    with op.batch_alter_table("change_plans") as b:
        b.alter_column("source", existing_type=sa.String(16), type_=sa.String(32), existing_nullable=False)
    # Migration-window marker (P11-DEP-03): which older application schemas may keep running against this one.
    # 0016 only adds nullable/defaulted columns and tables and widens columns, so 0015 code reads and writes it
    # unchanged (proved by tests/contract/test_rc18_migration_window.py). A contract migration sets readers to [].
    import datetime as _dt
    settings = sa.table("app_settings", sa.column("key", sa.String), sa.column("value", sa.JSON),
                        sa.column("updated_at", TS), sa.column("updated_by", sa.String))
    op.execute(settings.delete().where(settings.c.key == "schema:compat"))
    op.bulk_insert(settings, [{"key": "schema:compat", "updated_by": "migration 0016",
                               "updated_at": _dt.datetime.now(_dt.timezone.utc),
                               "value": {"head": "0016", "phase": "expand", "compatible_readers": ["0015"]}}])


def downgrade():
    raise RuntimeError("Restore the pre-upgrade backup; dropping model, token and integration tables loses records.")
