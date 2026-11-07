"""INM Phase 1 integration: audit chain fields, identity hardening, generic inventory graph,
event idempotency, heartbeats, leases, recommendations, release events.

Expand-only migration: it adds tables/columns and never drops or rewrites legacy data, so the previous
application release (SIMS baseline, schema 0003) can still run against it. Recorded in release.json as
`rollback_compatible_with: ["0003"]`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def _missing(table: str, cols: list[sa.Column]) -> list[sa.Column]:
    # Databases created before Alembic are built from the *current* models by session.create_schema(), so a
    # column may already exist; add only what is missing (keeps the legacy pre-migration upgrade path working).
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    return [c for c in cols if c.name not in have]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    user_cols = _missing('users', [
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('site_scope', sa.String(16), nullable=False, server_default='all')])
    if user_cols:
        with op.batch_alter_table('users') as b:
            for c in user_cols:
                b.add_column(c)

    audit_cols = _missing('audit_log', [sa.Column(n, sa.String(l), nullable=True) for n, l in (
        ('request_id', 64), ('correlation_id', 64), ('actor_role', 16), ('actor_kind', 16),
        ('session_id_hash', 64), ('channel', 16), ('prev_hash', 64), ('event_hash', 64))])
    have_idx = {i["name"] for i in insp.get_indexes('audit_log')}
    have_uq = {u["name"] for u in insp.get_unique_constraints('audit_log')}
    if audit_cols or 'ix_audit_log_request_id' not in have_idx or 'uq_audit_log_event_hash' not in have_uq:
        with op.batch_alter_table('audit_log') as b:
            for c in audit_cols:
                b.add_column(c)
            if 'ix_audit_log_request_id' not in have_idx:
                b.create_index('ix_audit_log_request_id', ['request_id'])
            if 'uq_audit_log_event_hash' not in have_uq:
                b.create_unique_constraint('uq_audit_log_event_hash', ['event_hash'])
    # Rows written before INM are an imported, immutable legacy copy (Appendix L.4: LegacyAuditEvent).
    op.execute("UPDATE audit_log SET channel = 'LEGACY' WHERE channel IS NULL")

    backup_cols = _missing('config_backups', [
        sa.Column('restorable', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('classification', sa.String(24), nullable=False, server_default='MIGRATED_REDACTED')])
    if backup_cols:
        with op.batch_alter_table('config_backups') as b:
            for c in backup_cols:
                b.add_column(c)
    # Every pre-existing backup is a redacted display copy: never restorable.
    op.execute("UPDATE config_backups SET restorable = false, classification = 'MIGRATED_REDACTED'")

    op.create_table('site_access_grants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('sites.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('granted_by', sa.String(64), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'site_id', name='uq_grant_user_site'))
    op.create_table('revoked_tokens',
        sa.Column('jti', sa.String(64), primary_key=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, index=True))
    op.create_table('audit_chain_head',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('last_hash', sa.String(64), nullable=False),
        sa.Column('last_audit_id', sa.Integer(), nullable=True))
    op.create_table('inm_sites',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('source_type', sa.String(8), nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_type', 'name', name='uq_inm_site_src_name'))
    op.create_table('inm_clusters',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('source_type', sa.String(8), nullable=False),
        sa.Column('site_id', sa.String(36), sa.ForeignKey('inm_sites.id'), nullable=True),
        sa.Column('legacy_site_id', sa.Integer(), sa.ForeignKey('sites.id', ondelete='SET NULL'), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('inm_devices',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_type', sa.String(8), nullable=False),
        sa.Column('environment', sa.String(8), nullable=False),
        sa.Column('vendor', sa.String(32), nullable=False),
        sa.Column('family', sa.String(32), nullable=False),
        sa.Column('model', sa.String(64), nullable=True),
        sa.Column('serial_number', sa.String(64), nullable=False),
        sa.Column('hostname', sa.String(100), nullable=True),
        sa.Column('firmware', sa.String(64), nullable=True),
        sa.Column('mgmt_addresses', sa.JSON(), nullable=True),
        sa.Column('site_id', sa.String(36), sa.ForeignKey('inm_sites.id'), nullable=True, index=True),
        sa.Column('cluster_id', sa.String(36), sa.ForeignKey('inm_clusters.id'), nullable=True, index=True),
        sa.Column('legacy_ap_id', sa.Integer(), nullable=True, index=True),
        sa.Column('legacy_site_id', sa.Integer(), nullable=True, index=True),
        sa.Column('lifecycle', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('adapter', sa.String(48), nullable=False),
        sa.Column('last_observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_type', 'vendor', 'serial_number', name='uq_device_src_vendor_serial'))
    op.create_table('device_capabilities',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.String(36), sa.ForeignKey('inm_devices.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('operation', sa.String(64), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('evidence', sa.String(255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('device_id', 'operation', name='uq_cap_device_op'))
    op.create_table('processed_events',
        sa.Column('consumer', sa.String(48), primary_key=True),
        sa.Column('event_id', sa.String(36), primary_key=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('event_records',
        sa.Column('event_id', sa.String(36), primary_key=True),
        sa.Column('event_type', sa.String(64), nullable=False, index=True),
        sa.Column('schema_version', sa.String(8), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('emitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=False, index=True),
        sa.Column('causation_id', sa.String(36), nullable=True),
        sa.Column('source_service', sa.String(32), nullable=False),
        sa.Column('source_type', sa.String(8), nullable=False),
        sa.Column('actor_type', sa.String(16), nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('site_id', sa.String(36), nullable=True),
        sa.Column('legacy_site_id', sa.Integer(), nullable=True, index=True),
        sa.Column('device_id', sa.String(36), nullable=True, index=True),
        sa.Column('data_quality', sa.String(12), nullable=False),
        sa.Column('integrity_hash', sa.String(64), nullable=False),
        sa.Column('summary', sa.String(300), nullable=True),
        sa.Column('delivery_count', sa.Integer(), nullable=False))
    op.create_table('service_heartbeats',
        sa.Column('service', sa.String(32), primary_key=True),
        sa.Column('instance', sa.String(64), nullable=False),
        sa.Column('version', sa.String(32), nullable=False),
        sa.Column('beat_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True))
    op.create_table('job_leases',
        sa.Column('job', sa.String(64), primary_key=True),
        sa.Column('holder', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_run_slot', sa.Integer(), nullable=True))
    op.create_table('recommendations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('legacy_site_id', sa.Integer(), nullable=True, index=True),
        sa.Column('device_id', sa.String(36), nullable=True),
        sa.Column('engine', sa.String(16), nullable=False),
        sa.Column('rule_id', sa.String(64), nullable=False),
        sa.Column('rule_version', sa.String(16), nullable=False),
        sa.Column('source_type', sa.String(8), nullable=False),
        sa.Column('risk', sa.String(12), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('input_window', sa.String(64), nullable=False),
        sa.Column('approval_state', sa.String(16), nullable=False),
        sa.Column('requested_by', sa.String(64), nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=False))
    op.create_table('release_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('operation', sa.String(16), nullable=False),
        sa.Column('operator', sa.String(64), nullable=False),
        sa.Column('version', sa.String(32), nullable=False),
        sa.Column('git_commit', sa.String(64), nullable=True),
        sa.Column('image_digests', sa.JSON(), nullable=True),
        sa.Column('schema_revisions', sa.JSON(), nullable=True),
        sa.Column('environment', sa.String(16), nullable=False),
        sa.Column('outcome', sa.String(16), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True))


def downgrade() -> None:
    # Deliberately not automatic: dropping audit/release tables would destroy evidence. Restore from backup.
    raise RuntimeError("0004 is expand-only; downgrade by restoring a pre-upgrade backup (deployment/centos9/restore.sh).")
