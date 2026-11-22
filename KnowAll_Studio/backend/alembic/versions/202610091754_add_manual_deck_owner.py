"""Add trusted manual-deck ownership without guessing legacy owners."""
from alembic import op
import sqlalchemy as sa

revision = '202610091754'
down_revision = '202610090850'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'decks' not in inspector.get_table_names():
        return
    if 'user_id' not in {c['name'] for c in inspector.get_columns('decks')}:
        op.add_column('decks', sa.Column('user_id', sa.String(36), nullable=True))
    if 'ix_decks_user_id' not in {i['name'] for i in inspector.get_indexes('decks')}:
        op.create_index('ix_decks_user_id', 'decks', ['user_id'])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'decks' not in inspector.get_table_names():
        return
    if 'user_id' not in {c['name'] for c in inspector.get_columns('decks')}:
        return
    if bind.execute(sa.text('SELECT 1 FROM decks WHERE user_id IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('存在私有牌组，拒绝删除归属字段；请先明确迁移策略。')
    if 'ix_decks_user_id' in {i['name'] for i in inspector.get_indexes('decks')}:
        op.drop_index('ix_decks_user_id', table_name='decks')
    with op.batch_alter_table('decks') as batch:
        batch.drop_column('user_id')
