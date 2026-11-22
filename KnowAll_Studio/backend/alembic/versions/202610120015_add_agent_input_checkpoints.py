"""Add separate encrypted input snapshots; no automatic execution replay."""
from alembic import op
import sqlalchemy as sa

revision = '202610120015'
down_revision = '202610112250'
branch_labels = None
depends_on = None


def upgrade():
    if not op.get_context().as_sql and sa.inspect(op.get_bind()).has_table('agent_input_checkpoints'):
        inspector=sa.inspect(op.get_bind())
        columns={column['name']:column for column in inspector.get_columns('agent_input_checkpoints')}
        expected={'run_id':sa.String,'user_id':sa.String,'version':sa.Integer,'input_ciphertext':sa.Text,'created_at':sa.DateTime}
        if (set(columns)!=set(expected) or
                any(not isinstance(columns[name]['type'],kind) or columns[name]['nullable'] for name,kind in expected.items()) or
                inspector.get_pk_constraint('agent_input_checkpoints')['constrained_columns']!=['run_id'] or
                columns['run_id']['type'].length!=36 or columns['user_id']['type'].length!=36):
            raise RuntimeError('Existing input checkpoint table is incompatible; refusing to rewrite it.')
        return
    op.create_table('agent_input_checkpoints',
        sa.Column('run_id',sa.String(36),primary_key=True),
        sa.Column('user_id',sa.String(36),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('input_ciphertext',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.CheckConstraint('version = 1',name='ck_agent_input_checkpoints_version'))
    op.create_index('ix_agent_input_checkpoints_user_id','agent_input_checkpoints',['user_id'])


def downgrade():
    op.drop_index('ix_agent_input_checkpoints_user_id',table_name='agent_input_checkpoints')
    op.drop_table('agent_input_checkpoints')
