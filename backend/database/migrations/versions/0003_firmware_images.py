"""firmware images

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 01:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('firmware_images',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=160), nullable=False),
    sa.Column('ap_class', sa.String(length=32), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('uploaded_by', sa.String(length=64), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('filename')
    )


def downgrade() -> None:
    op.drop_table('firmware_images')
