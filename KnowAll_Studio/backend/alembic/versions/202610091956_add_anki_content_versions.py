"""Persist content/export versions without changing legacy Anki identity."""
from alembic import op
import sqlalchemy as sa

revision = '202610091956'
down_revision = '202610091754'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'flashcards' not in inspector.get_table_names():
        return
    names = {c['name'] for c in inspector.get_columns('flashcards')}
    columns = [sa.Column('content_version', sa.Integer(), nullable=False, server_default='1'),
               sa.Column('anki_export_version', sa.Integer(), nullable=True),
               sa.Column('anki_export_mtime', sa.BigInteger(), nullable=True)]
    for column in columns:
        if column.name not in names:
            op.add_column('flashcards', column)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'flashcards' not in inspector.get_table_names():
        return
    names = {c['name'] for c in inspector.get_columns('flashcards')}
    if 'anki_export_mtime' in names and bind.execute(sa.text(
            'SELECT 1 FROM flashcards WHERE anki_export_mtime IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('已持久保存 Anki 导出版本，拒绝删除；请先明确迁移策略。')
    with op.batch_alter_table('flashcards') as batch:
        for name in ['anki_export_mtime', 'anki_export_version', 'content_version']:
            if name in names:
                batch.drop_column(name)
