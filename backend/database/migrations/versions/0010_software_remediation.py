"""1.2.0 software remediation (expand-only): transactional event outbox, correlation ids on collection runs /
alerts / executions, durable discovery jobs, persistent onboarding workflows, encrypted change-recovery journal,
device groups, alert notification channels and deliveries.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'event_outbox',
        sa.Column('event_id', sa.String(36), primary_key=True),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=False),
        sa.Column('device_id', sa.String(36), nullable=True),
        sa.Column('envelope', sa.Text(), nullable=False),
        sa.Column('state', sa.String(12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(300), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('next_attempt_at', TS, nullable=True),
        sa.Column('delivered_at', TS, nullable=True),
    )
    for col in ('event_type', 'correlation_id', 'device_id', 'state', 'created_at'):
        op.create_index(f'ix_event_outbox_{col}', 'event_outbox', [col])

    with op.batch_alter_table('collection_runs') as b:
        b.add_column(sa.Column('event_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('correlation_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('expected_points', sa.Integer(), nullable=True))
        b.create_index('ix_collection_runs_event_id', ['event_id'])
        b.create_index('ix_collection_runs_correlation_id', ['correlation_id'])
    with op.batch_alter_table('change_executions') as b:
        b.add_column(sa.Column('correlation_id', sa.String(36), nullable=True))
        b.create_index('ix_change_executions_correlation_id', ['correlation_id'])
    with op.batch_alter_table('inm_alerts') as b:
        b.add_column(sa.Column('correlation_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('event_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('entity', sa.String(64), nullable=True))
        b.create_index('ix_inm_alerts_correlation_id', ['correlation_id'])
    with op.batch_alter_table('onboarding_discoveries') as b:
        b.add_column(sa.Column('targets', sa.JSON(), nullable=True))
        b.add_column(sa.Column('next_index', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('lease_owner', sa.String(64), nullable=True))
        b.add_column(sa.Column('lease_expires_at', TS, nullable=True))
        b.add_column(sa.Column('heartbeat_at', TS, nullable=True))
        b.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('rate_per_s', sa.Integer(), nullable=False, server_default='16'))
        b.add_column(sa.Column('finished_at', TS, nullable=True))
        b.add_column(sa.Column('workflow_id', sa.String(36), nullable=True))
        b.create_index('ix_onboarding_discoveries_workflow_id', ['workflow_id'])
    # A discovery left SCANNING by the old in-process thread can never finish: re-queue it from the start.
    op.execute("UPDATE onboarding_discoveries SET status = 'FAILED' WHERE status = 'SCANNING'")

    op.create_table(
        'onboarding_workflows',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('site_id', sa.String(36), nullable=True),
        sa.Column('site_name', sa.String(100), nullable=True),
        sa.Column('device_family', sa.String(32), nullable=True),
        sa.Column('step', sa.String(32), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('mode', sa.String(8), nullable=True),
        sa.Column('credential_id', sa.String(36), nullable=True),
        sa.Column('discovery_id', sa.String(36), nullable=True),
        sa.Column('device_ids', sa.JSON(), nullable=False),
        sa.Column('template_scope', sa.JSON(), nullable=False),
        sa.Column('plan_ids', sa.JSON(), nullable=False),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('outcome', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(64), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('updated_at', TS, nullable=False),
    )
    op.create_index('ix_onboarding_workflows_state', 'onboarding_workflows', ['state'])

    op.create_table(
        'change_recovery_journal',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('execution_id', sa.String(36), nullable=False),
        sa.Column('plan_id', sa.String(36), nullable=False),
        sa.Column('device_id', sa.String(36), nullable=False),
        sa.Column('environment', sa.String(16), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('strategy', sa.String(32), nullable=False),
        sa.Column('payload_enc', sa.Text(), nullable=False),
        sa.Column('last_step', sa.String(48), nullable=True),
        sa.Column('owner', sa.String(64), nullable=True),
        sa.Column('heartbeat_at', TS, nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('updated_at', TS, nullable=False),
        sa.Column('resolved_by', sa.String(64), nullable=True),
        sa.Column('notes', sa.String(500), nullable=True),
    )
    for col in ('execution_id', 'plan_id', 'device_id', 'state'):
        op.create_index(f'ix_change_recovery_journal_{col}', 'change_recovery_journal', [col])

    with op.batch_alter_table('inm_config_templates') as b:
        b.add_column(sa.Column('group_id', sa.String(36), nullable=True))
        b.create_index('ix_inm_config_templates_group_id', ['group_id'])
    op.create_table(
        'device_groups',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('family', sa.String(32), nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=False),
        sa.Column('created_at', TS, nullable=False),
    )
    op.create_table(
        'device_group_members',
        sa.Column('group_id', sa.String(36), sa.ForeignKey('device_groups.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('device_id', sa.String(36), sa.ForeignKey('inm_devices.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('added_by', sa.String(64), nullable=False),
        sa.Column('added_at', TS, nullable=False),
    )

    op.create_table(
        'notification_channels',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('target', sa.String(300), nullable=False),
        sa.Column('min_severity', sa.String(12), nullable=False),
        sa.Column('escalate_after_minutes', sa.Integer(), nullable=True),
        sa.Column('escalation_channel_id', sa.String(36), nullable=True),
        sa.Column('include_demo', sa.Boolean(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(64), nullable=False),
        sa.Column('created_at', TS, nullable=False),
    )
    op.create_table(
        'notification_deliveries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dedupe_key', sa.String(160), nullable=False, unique=True),
        sa.Column('channel_id', sa.String(36), nullable=False),
        sa.Column('alert_id', sa.String(36), nullable=True),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('state', sa.String(10), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(300), nullable=True),
        sa.Column('correlation_id', sa.String(36), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('next_attempt_at', TS, nullable=True),
        sa.Column('sent_at', TS, nullable=True),
    )
    for col in ('channel_id', 'alert_id', 'state'):
        op.create_index(f'ix_notification_deliveries_{col}', 'notification_deliveries', [col])


def downgrade() -> None:
    raise RuntimeError("0010 is expand-only; restore the pre-update backup instead of downgrading.")
