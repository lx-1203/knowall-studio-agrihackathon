"""Persist account ownership without deleting historical conversations."""
from alembic import op
import sqlalchemy as sa

revision = '202610102130'
down_revision = '202610101854'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError('Conversation ownership requires online schema inspection.')
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('conversations'):
        # Existing initial revision is empty; fresh init creates current models.
        return
    columns = {c['name']: c for c in inspector.get_columns('conversations')}
    if 'user_id' not in columns:
        owner = op.get_context().config.attributes.get('legacy_conversation_owner', 'local_user')
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 36:
            raise RuntimeError('Invalid legacy conversation owner.')
        # One DDL operation also backfills existing rows. MySQL commits DDL;
        # retain the chosen owner even if stopped before creating the index.
        op.add_column('conversations', sa.Column('user_id', sa.String(36), nullable=False, server_default=owner))
    else:
        column = columns['user_id']
        if not isinstance(column['type'], sa.String) or column['type'].length != 36 or column['nullable']:
            raise RuntimeError('Conversation owner schema is incompatible; refusing to change ownership.')
    # Keep the legacy default (personal cloud owner or local_user) so SQLite
    # need not rebuild a parent table referenced by messages with FK enabled.
    # Current route inserts always supply identity explicitly.
    if 'ix_conversations_user_id' not in {i['name'] for i in inspector.get_indexes('conversations')}:
        op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])


def downgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('conversations'):
        return
    if bind.execute(sa.text("SELECT 1 FROM conversations WHERE user_id <> 'local_user' LIMIT 1")).first():
        raise RuntimeError('拒绝删除已有账号会话归属；需显式数据迁移。')
    with op.batch_alter_table('conversations') as batch:
        batch.drop_index('ix_conversations_user_id')
        batch.drop_column('user_id')
