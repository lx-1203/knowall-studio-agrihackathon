"""Track encoder upgrades independently of card content and review state."""
from alembic import op
import sqlalchemy as sa

revision = '202610092122'
down_revision = '202610091956'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'flashcards' in inspector.get_table_names() and 'anki_export_format' not in {
            c['name'] for c in inspector.get_columns('flashcards')}:
        op.add_column('flashcards', sa.Column('anki_export_format', sa.Integer(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'flashcards' not in inspector.get_table_names() or 'anki_export_format' not in {
            c['name'] for c in inspector.get_columns('flashcards')}:
        return
    if bind.execute(sa.text('SELECT 1 FROM flashcards WHERE anki_export_format IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('已持久保存 Anki 导出格式版本，拒绝删除；请先明确迁移策略。')
    with op.batch_alter_table('flashcards') as batch:
        batch.drop_column('anki_export_format')
