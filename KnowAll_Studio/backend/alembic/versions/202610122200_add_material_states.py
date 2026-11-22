"""Private notes/text positions with durable operation receipts."""
from alembic import op
import sqlalchemy as sa
revision = '202610122200'
down_revision = '202610120015'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('material_states',
        sa.Column('user_id',sa.String(36),primary_key=True),
        sa.Column('document_id',sa.String(36),primary_key=True),
        sa.Column('kind',sa.String(16),primary_key=True),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('source_version',sa.String(64),nullable=True),
        sa.Column('updated_at',sa.DateTime(),nullable=False))
    op.create_table('material_state_operations',
        sa.Column('user_id',sa.String(36),primary_key=True),
        sa.Column('document_id',sa.String(36),primary_key=True),
        sa.Column('kind',sa.String(16),primary_key=True),
        sa.Column('operation_id',sa.String(80),primary_key=True),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False))

def downgrade():
    op.drop_table('material_state_operations')
    op.drop_table('material_states')
