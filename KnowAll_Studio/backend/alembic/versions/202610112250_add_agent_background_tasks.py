"""Add background stop metadata; preserve all existing Agent receipts."""
from alembic import op
import sqlalchemy as sa

revision = '202610112250'
down_revision = '202610112145'
branch_labels = None
depends_on = None


def upgrade():
    if not op.get_context().as_sql and sa.inspect(op.get_bind()).has_table('agent_background_tasks'):
        existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('agent_background_tasks')}
        if existing != {'run_id', 'user_id', 'cancel_requested', 'created_at'}:
            raise RuntimeError('Existing background control table is incompatible; refusing to rewrite it.')
        return
    op.create_table('agent_background_tasks',
        sa.Column('run_id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_agent_background_tasks_user_id', 'agent_background_tasks', ['user_id'])


def downgrade():
    # Control has no prompt, answer, source or receipt content. An explicit
    # downgrade removes the opt-in control capability, keeping AgentRun intact.
    op.drop_index('ix_agent_background_tasks_user_id', table_name='agent_background_tasks')
    op.drop_table('agent_background_tasks')
