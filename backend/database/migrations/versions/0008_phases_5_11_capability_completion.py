"""Phases 5-11 capability completion (WP-02..WP-12) — expand-only.

Creates collection, attribution, vault, change-control, replacement, learning,
per-device optimization and reporting tables. Grants no device-write capability.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('auth_session_evidence',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_serial', sa.String(length=64), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('external_session_id', sa.String(length=128), nullable=True),
    sa.Column('recorded_by', sa.String(length=64), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('auth_session_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auth_session_evidence_device_serial'), ['device_serial'], unique=False)

    op.create_table('change_attributions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('log_event_id', sa.String(length=36), nullable=True),
    sa.Column('change_plan_id', sa.String(length=36), nullable=True),
    sa.Column('change_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('level', sa.String(length=12), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=True),
    sa.Column('change_type', sa.String(length=16), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('candidates', sa.JSON(), nullable=False),
    sa.Column('supersedes_id', sa.String(length=36), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('change_attributions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_attributions_change_at'), ['change_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_attributions_device_id'), ['device_id'], unique=False)

    op.create_table('change_plans',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('operations', sa.JSON(), nullable=False),
    sa.Column('capability_report', sa.JSON(), nullable=False),
    sa.Column('plan_hash', sa.String(length=64), nullable=False),
    sa.Column('expected_config_hash', sa.String(length=64), nullable=True),
    sa.Column('firmware_at_request', sa.String(length=64), nullable=True),
    sa.Column('rollback_plan', sa.JSON(), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('requester', sa.String(length=64), nullable=False),
    sa.Column('approver', sa.String(length=64), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('change_plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_plans_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_plans_device_id'), ['device_id'], unique=False)

    op.create_table('config_artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('serial', sa.String(length=64), nullable=False),
    sa.Column('vendor', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('firmware', sa.String(length=64), nullable=True),
    sa.Column('fidelity', sa.String(length=32), nullable=False),
    sa.Column('capture_source', sa.String(length=32), nullable=False),
    sa.Column('trigger', sa.String(length=24), nullable=False),
    sa.Column('object_path', sa.String(length=255), nullable=False),
    sa.Column('wrapped_key', sa.Text(), nullable=False),
    sa.Column('key_id', sa.String(length=32), nullable=False),
    sa.Column('plaintext_sha256', sa.String(length=64), nullable=False),
    sa.Column('ciphertext_sha256', sa.String(length=64), nullable=False),
    sa.Column('manifest', sa.JSON(), nullable=False),
    sa.Column('signature', sa.String(length=64), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('retention_days', sa.Integer(), nullable=False),
    sa.Column('restore_state', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('config_artifacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_config_artifacts_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_config_artifacts_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_config_artifacts_plaintext_sha256'), ['plaintext_sha256'], unique=False)

    op.create_table('decision_feedback',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('recommendation_id', sa.String(length=36), nullable=False),
    sa.Column('operator', sa.String(length=64), nullable=False),
    sa.Column('decision', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('recommendation_id', name='uq_feedback_recommendation')
    )
    with op.batch_alter_table('decision_feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_decision_feedback_recommendation_id'), ['recommendation_id'], unique=False)

    op.create_table('device_log_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=True),
    sa.Column('device_serial', sa.String(length=64), nullable=True),
    sa.Column('source_ip', sa.String(length=45), nullable=False),
    sa.Column('vendor', sa.String(length=32), nullable=False),
    sa.Column('event_code', sa.String(length=16), nullable=True),
    sa.Column('severity', sa.String(length=12), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('is_config_change', sa.Boolean(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('parser_version', sa.String(length=32), nullable=False),
    sa.Column('transport_quality', sa.String(length=20), nullable=False),
    sa.Column('raw_hash', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_hash')
    )
    with op.batch_alter_table('device_log_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_device_log_events_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_device_log_events_device_serial'), ['device_serial'], unique=False)
        batch_op.create_index(batch_op.f('ix_device_log_events_observed_at'), ['observed_at'], unique=False)

    op.create_table('execution_decisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('plan_id', sa.String(length=36), nullable=True),
    sa.Column('enrollment_id', sa.String(length=36), nullable=True),
    sa.Column('recommendation_id', sa.String(length=36), nullable=True),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('proposed_diff_hash', sa.String(length=64), nullable=True),
    sa.Column('global_lock_state', sa.String(length=16), nullable=False),
    sa.Column('decision', sa.String(length=8), nullable=False),
    sa.Column('guards', sa.JSON(), nullable=False),
    sa.Column('denial_reason', sa.Text(), nullable=True),
    sa.Column('evaluated_by', sa.String(length=64), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('execution_decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_execution_decisions_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_execution_decisions_enrollment_id'), ['enrollment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_execution_decisions_evaluated_at'), ['evaluated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_execution_decisions_plan_id'), ['plan_id'], unique=False)

    op.create_table('ml_models',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('algorithm', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('dataset_sha256', sa.String(length=64), nullable=False),
    sa.Column('split', sa.JSON(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('trained_by', sa.String(length=64), nullable=False),
    sa.Column('trained_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by', sa.String(length=64), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('optimization_control',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('emergency_stop', sa.Boolean(), nullable=False),
    sa.Column('changed_by', sa.String(length=64), nullable=True),
    sa.Column('changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('optimization_enrollments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('immutable_serial', sa.String(length=64), nullable=False),
    sa.Column('vendor_model', sa.String(length=100), nullable=True),
    sa.Column('firmware_bound', sa.String(length=64), nullable=True),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('policy_version', sa.Integer(), nullable=False),
    sa.Column('allowed_operation_types', sa.JSON(), nullable=False),
    sa.Column('value_constraints', sa.JSON(), nullable=False),
    sa.Column('permitted_windows', sa.JSON(), nullable=False),
    sa.Column('min_observation_quality', sa.String(length=12), nullable=False),
    sa.Column('baseline_config_hash', sa.String(length=64), nullable=True),
    sa.Column('cooldown_seconds', sa.Integer(), nullable=False),
    sa.Column('max_actions_per_day', sa.Integer(), nullable=False),
    sa.Column('impact_stop_threshold', sa.JSON(), nullable=False),
    sa.Column('valid_from_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('requester_id', sa.String(length=64), nullable=False),
    sa.Column('independent_approver_id', sa.String(length=64), nullable=True),
    sa.Column('approved_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('status_reason', sa.String(length=200), nullable=True),
    sa.Column('last_action_id', sa.String(length=36), nullable=True),
    sa.Column('revoked_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('optimization_enrollments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_optimization_enrollments_device_id'), ['device_id'], unique=False)

    op.create_table('outcome_evaluations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('recommendation_id', sa.String(length=36), nullable=False),
    sa.Column('action_applied', sa.Boolean(), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('before_window', sa.JSON(), nullable=False),
    sa.Column('after_window', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('evaluated_by', sa.String(length=64), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('outcome_evaluations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outcome_evaluations_recommendation_id'), ['recommendation_id'], unique=False)

    op.create_table('replacement_plans',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('old_device_id', sa.String(length=36), nullable=False),
    sa.Column('new_device_id', sa.String(length=36), nullable=False),
    sa.Column('artifact_id', sa.String(length=36), nullable=True),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('blockers', sa.JSON(), nullable=False),
    sa.Column('identity_map', sa.JSON(), nullable=False),
    sa.Column('plan', sa.JSON(), nullable=False),
    sa.Column('requester', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('replacement_plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_replacement_plans_old_device_id'), ['old_device_id'], unique=False)

    op.create_table('report_exports',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('requested_by', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.String(length=200), nullable=False),
    sa.Column('range_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('range_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('signature', sa.String(length=64), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('restore_validations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('artifact_id', sa.String(length=36), nullable=False),
    sa.Column('target', sa.JSON(), nullable=False),
    sa.Column('result', sa.String(length=16), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('validated_by', sa.String(length=64), nullable=False),
    sa.Column('validated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['artifact_id'], ['config_artifacts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('restore_validations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_restore_validations_artifact_id'), ['artifact_id'], unique=False)

    op.create_table('collection_plans',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('site_id', sa.String(length=36), nullable=True),
    sa.Column('credential_profile_id', sa.String(length=36), nullable=True),
    sa.Column('adapter_key', sa.String(length=48), nullable=False),
    sa.Column('metric_families', sa.JSON(), nullable=False),
    sa.Column('interval_seconds', sa.Integer(), nullable=False),
    sa.Column('window_start_hour', sa.Integer(), nullable=True),
    sa.Column('window_end_hour', sa.Integer(), nullable=True),
    sa.Column('max_concurrency', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('next_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_failure_category', sa.String(length=24), nullable=True),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['inm_devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', name='uq_collection_plan_device')
    )
    with op.batch_alter_table('collection_plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_collection_plans_device_id'), ['device_id'], unique=False)

    op.create_table('collection_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('plan_id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('metric_family', sa.String(length=24), nullable=False),
    sa.Column('scheduled_window', sa.Integer(), nullable=False),
    sa.Column('dedupe_key', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('failure_category', sa.String(length=24), nullable=True),
    sa.Column('points_written', sa.Integer(), nullable=False),
    sa.Column('worker', sa.String(length=64), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retry_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['plan_id'], ['collection_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key')
    )
    with op.batch_alter_table('collection_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_collection_runs_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_collection_runs_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_collection_runs_started_at'), ['started_at'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    raise RuntimeError("0008 is expand-only; restore the encrypted pre-upgrade backup instead of downgrading.")
