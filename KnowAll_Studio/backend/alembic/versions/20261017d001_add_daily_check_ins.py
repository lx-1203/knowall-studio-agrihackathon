"""Add explicit daily check-ins without modifying existing learning records.

Revision ID: 20261017d001
Revises: 202610122200
"""
from alembic import op
import sqlalchemy as sa

revision = '20261017d001'
down_revision = '202610122200'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table('daily_check_ins'):
        return
    op.create_table(
        'daily_check_ins',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('check_in_date', sa.Date(), nullable=False),
        sa.Column('note', sa.String(300), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'check_in_date', name='uq_daily_check_in_user_date'),
    )


def downgrade():
    op.drop_table('daily_check_ins')
