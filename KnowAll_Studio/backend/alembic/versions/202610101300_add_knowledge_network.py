"""Add central identities, private evidence, learning and authority lookup state."""
from alembic import op
import sqlalchemy as sa

revision = '202610101300'
down_revision = '202610100930'
branch_labels = None
depends_on = None


def tables():
    metadata = sa.MetaData()
    # Referenced legacy tables are declared only, never created or modified here.
    sa.Table('documents', metadata, sa.Column('id', sa.String(36), primary_key=True))
    sa.Table('document_chunks', metadata, sa.Column('id', sa.String(36), primary_key=True))
    concept = sa.Table('network_concepts', metadata,
        sa.Column('id', sa.String(68), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('subject', sa.String(80), nullable=False, index=True),
        sa.Column('topic', sa.String(120), nullable=False),
        sa.Column('public_content', sa.JSON(none_as_null=True), nullable=True),
        sa.Column('review_status', sa.String(24), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    jobs = sa.Table('network_extraction_jobs', metadata,
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False, index=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('subject', sa.String(80), nullable=False),
        sa.Column('model', sa.String(128), nullable=False),
        sa.Column('cache_key', sa.String(64), nullable=False, unique=True),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('point_count', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))
    sources = sa.Table('network_concept_sources', metadata,
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('concept_id', sa.String(68), sa.ForeignKey('network_concepts.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('user_id', sa.String(36), nullable=False, index=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('chunk_id', sa.String(36), sa.ForeignKey('document_chunks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('quote_hash', sa.String(64), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('reviewed', sa.Boolean(), nullable=False),
        sa.Column('summary_id', sa.String(36), nullable=True),
        sa.Column('legacy_node_id', sa.String(36), nullable=True),
        sa.UniqueConstraint('concept_id', 'user_id', 'document_id', 'chunk_id', 'quote_hash', name='uq_network_source'))
    relations = sa.Table('network_concept_relations', metadata,
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False, index=True),
        sa.Column('source_id', sa.String(68), sa.ForeignKey('network_concepts.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('target_id', sa.String(68), sa.ForeignKey('network_concepts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('relation_type', sa.String(24), nullable=False),
        sa.Column('is_public', sa.Boolean(), nullable=False),
        sa.Column('reviewed', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('user_id', 'source_id', 'target_id', 'relation_type', name='uq_network_relation'))
    learning = sa.Table('network_concept_learning', metadata,
        sa.Column('user_id', sa.String(36), primary_key=True),
        sa.Column('concept_id', sa.String(68), sa.ForeignKey('network_concepts.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('saved', sa.Boolean(), nullable=False),
        sa.Column('mastery', sa.String(24), nullable=False),
        sa.Column('note', sa.Text(), nullable=False))
    authority = sa.Table('network_authority_searches', metadata,
        sa.Column('concept_id', sa.String(68), sa.ForeignKey('network_concepts.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('sources', sa.JSON(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False))
    return [concept, jobs, sources, relations, learning, authority]


def upgrade():
    for table in tables():
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    present = set(sa.inspect(op.get_bind()).get_table_names())
    ordered = tables()
    for table in ordered:
        if table.name in present and op.get_bind().scalar(sa.select(sa.func.count()).select_from(table)):
            raise RuntimeError('中央知识网络已有数据，拒绝删除；请先备份并迁移知识内容。')
    for table in reversed(ordered):
        table.drop(op.get_bind(), checkfirst=True)
