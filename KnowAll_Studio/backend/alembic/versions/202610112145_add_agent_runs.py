"""Add durable Agent receipts without rewriting existing application data."""
from alembic import op
import sqlalchemy as sa

revision='202610112145'
down_revision='202610102130'
branch_labels=None
depends_on=None


def upgrade():
    if not op.get_context().as_sql and sa.inspect(op.get_bind()).has_table('agent_runs'):
        # Fresh init may already have created current ORM metadata.
        existing={column['name'] for column in sa.inspect(op.get_bind()).get_columns('agent_runs')}
        required={'id','user_id','conversation_id','assistant_message_id','status','scope','model','sources','steps','error_code','created_at','completed_at'}
        if not required.issubset(existing):raise RuntimeError('Existing AgentRun table is incompatible; refusing to rewrite it.')
        return
    op.create_table('agent_runs',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('user_id',sa.String(36),nullable=False),
        sa.Column('conversation_id',sa.String(36),nullable=True),
        sa.Column('assistant_message_id',sa.String(36),nullable=True,unique=True),
        sa.Column('status',sa.String(32),nullable=False),
        sa.Column('scope',sa.String(32),nullable=False),
        sa.Column('model',sa.String(128),nullable=False),
        sa.Column('sources',sa.JSON(),nullable=False),
        sa.Column('steps',sa.JSON(),nullable=False),
        sa.Column('error_code',sa.String(64),nullable=True),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.Column('completed_at',sa.DateTime(),nullable=True),
        sa.CheckConstraint("status IN ('queued','running','waiting_user','completed','partial','failed','cancelled')",name='ck_agent_runs_status'))
    op.create_index('ix_agent_runs_user_id','agent_runs',['user_id'])
    op.create_index('ix_agent_runs_owner_conversation','agent_runs',['user_id','conversation_id','created_at'])


def downgrade():
    raise RuntimeError('Refusing to delete durable Agent receipts; use an explicit archival migration.')
