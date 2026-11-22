"""Retain namespaced KAF source IDs and legacy duplicate publications."""
from alembic import op
import sqlalchemy as sa

revision = '202610101748'
down_revision = '202610101615'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('kaf_files') as batch:
        batch.alter_column('source_doc_id', existing_type=sa.String(36),
                           type_=sa.String(64), existing_nullable=True)
        batch.create_index('ix_kaf_files_source_doc_id', ['source_doc_id'], unique=False)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError('Source IDs require an online 36-character downgrade safety check.')
    bind = op.get_bind()
    table = sa.table('kaf_files', sa.column('source_doc_id', sa.String(64)))
    length = sa.func.char_length if bind.dialect.name == 'mysql' else sa.func.length
    if bind.scalar(sa.select(table.c.source_doc_id).where(length(table.c.source_doc_id)>36).limit(1)) is not None:
        raise RuntimeError('KAF source IDs exceed 36 characters; refusing a truncating downgrade. Back up and migrate these IDs first.')
    with op.batch_alter_table('kaf_files') as batch:
        batch.drop_index('ix_kaf_files_source_doc_id')
        batch.alter_column('source_doc_id', existing_type=sa.String(64),
                           type_=sa.String(36), existing_nullable=True)
