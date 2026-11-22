"""Claim sources without deleting historical duplicate publications."""
from alembic import op
import sqlalchemy as sa

revision = '202610101854'
down_revision = '202610101748'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError('KAF source claims require online history/collation inspection.')
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c['name']: c for c in inspector.get_columns('kaf_files')}
    source = columns['source_doc_id']
    claim_type = sa.String(64, collation=getattr(source['type'], 'collation', None))
    # MySQL DDL commits independently: resume after a stopped migration instead
    # of failing forever on a column already added by this revision.
    if 'source_claim' in columns:
        existing = columns['source_claim']
        if getattr(existing['type'], 'length', None)!=64 or not existing['nullable']:
            raise RuntimeError('Existing KAF source_claim has an incompatible schema.')
        if any(c['name']=='uq_kaf_files_source_claim' for c in inspector.get_unique_constraints('kaf_files')):
            return
    else:
        op.add_column('kaf_files', sa.Column('source_claim', claim_type, nullable=True))
    table = sa.table('kaf_files', sa.column('id'), sa.column('source_doc_id'), sa.column('source_claim'))
    # GROUP BY materializes the derived table on MySQL; keep one native-identity
    # anchor while preserving every original row, file and grant reference.
    length = sa.func.char_length if bind.dialect.name == 'mysql' else sa.func.length
    anchors = sa.select(sa.func.min(table.c.id).label('anchor_id')).where(
        table.c.source_doc_id.is_not(None), length(table.c.source_doc_id)>0
    ).group_by(table.c.source_doc_id).subquery()
    bind.execute(table.update().where(table.c.id.in_(sa.select(anchors.c.anchor_id))).values(source_claim=table.c.source_doc_id))
    with op.batch_alter_table('kaf_files') as batch:
        batch.create_unique_constraint('uq_kaf_files_source_claim', ['source_claim'])


def downgrade():
    with op.batch_alter_table('kaf_files') as batch:
        batch.drop_constraint('uq_kaf_files_source_claim', type_='unique')
        batch.drop_column('source_claim')
