"""Widen model references for account-owned connections; preserve existing data."""
from alembic import op
import sqlalchemy as sa

revision = '20261020a001'
down_revision = '20261017d001'
branch_labels = None
depends_on = None

COLUMNS = (
    ('messages', 'model_used'), ('api_call_logs', 'model_name'),
    ('api_cache', 'model_used'), ('knowledge_summaries', 'model_used'),
    ('agent_runs', 'model'), ('network_extraction_jobs', 'model'),
)


def upgrade():
    connection = op.get_bind()
    # SQLite does not enforce VARCHAR length. Avoid rebuilding populated tables.
    if connection.dialect.name == 'sqlite':
        return
    inspector = sa.inspect(connection)
    for table, column in COLUMNS:
        if not inspector.has_table(table):
            continue
        existing = next(c for c in inspector.get_columns(table) if c['name'] == column)
        if (getattr(existing['type'], 'length', None) or 0) < 256:
            op.alter_column(table, column, type_=sa.String(256),
                            existing_type=existing['type'], existing_nullable=existing['nullable'])


def downgrade():
    # Retain wider fields: shrinking could truncate references written since upgrade.
    pass
