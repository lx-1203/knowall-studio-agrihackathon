"""Preserve generated schedules without inventing schedules for legacy plans."""
from alembic import op
import sqlalchemy as sa

revision = '202610090850'
down_revision = '202610090423'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'study_plans' not in inspector.get_table_names():
        return  # init_db subsequently creates missing tables from the models.
    columns = {column['name'] for column in inspector.get_columns('study_plans')}
    for name in ('short_term_plan', 'long_term_plan'):
        if name not in columns:
            op.add_column('study_plans', sa.Column(name, sa.JSON(), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if 'study_plans' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('study_plans')}
    for name in ('long_term_plan', 'short_term_plan'):
        if name in columns:
            op.drop_column('study_plans', name)
