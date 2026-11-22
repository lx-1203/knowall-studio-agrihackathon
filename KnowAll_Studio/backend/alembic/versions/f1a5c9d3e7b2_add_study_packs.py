"""Add user-owned source-versioned learning packs without changing existing rows.

Revision ID: f1a5c9d3e7b2
Revises: e2a4c6b8d0f1
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a5c9d3e7b2'
down_revision = 'e2a4c6b8d0f1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'study_packs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('cache_key', sa.String(64), nullable=True),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('sections', sa.JSON(), nullable=False),
        sa.Column('summary_id', sa.String(36), sa.ForeignKey('knowledge_summaries.id', ondelete='SET NULL')),
        sa.Column('tree_id', sa.String(36), sa.ForeignKey('knowledge_trees.id', ondelete='SET NULL')),
        sa.Column('deck_id', sa.String(36), sa.ForeignKey('decks.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'cache_key', name='uq_study_pack_user_cache'),
    )
    op.create_index('ix_study_packs_user_id', 'study_packs', ['user_id'])
    op.create_index('ix_study_packs_document_id', 'study_packs', ['document_id'])


def downgrade():
    op.drop_index('ix_study_packs_document_id', table_name='study_packs')
    op.drop_index('ix_study_packs_user_id', table_name='study_packs')
    op.drop_table('study_packs')
