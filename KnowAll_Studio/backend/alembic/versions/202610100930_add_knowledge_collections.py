"""Add owned knowledge-only compilations without changing source documents."""
from alembic import op
import sqlalchemy as sa

revision = '202610100930'
down_revision = '202610100004'
branch_labels = None
depends_on = None


def upgrade():
    if 'knowledge_collections' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('knowledge_collections',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('user_id', sa.String(36), nullable=False),
            sa.Column('title', sa.String(120), nullable=False),
            sa.Column('subject', sa.String(80), nullable=False),
            sa.Column('points', sa.JSON(), nullable=False),
            sa.Column('provenance', sa.JSON(), nullable=False),
            sa.Column('review_status', sa.String(24), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False))
        op.create_index('ix_knowledge_collections_user_id', 'knowledge_collections', ['user_id'])


def downgrade():
    if 'knowledge_collections' in sa.inspect(op.get_bind()).get_table_names():
        count = op.get_bind().scalar(sa.text('SELECT COUNT(*) FROM knowledge_collections'))
        if count:
            raise RuntimeError('已有知识点集锦，拒绝删除；请先迁移保存的知识内容。')
        op.drop_table('knowledge_collections')
