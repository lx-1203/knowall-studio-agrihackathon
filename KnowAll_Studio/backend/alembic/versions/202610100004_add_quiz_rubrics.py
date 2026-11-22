"""Preserve deterministic fill-blank and scoring-point metadata."""
from alembic import op
import sqlalchemy as sa

revision = '202610100004'
down_revision = '202610092122'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'question_bank' not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns('question_bank')}
    for name in ('blanks', 'scoring_points'):
        if name not in columns:
            op.add_column('question_bank', sa.Column(name, sa.JSON(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'question_bank' not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns('question_bank')}
    present = [n for n in ('blanks', 'scoring_points') if n in columns]
    if not present:
        return
    # Inspect decoded JSON on every supported dialect; a nonempty rubric
    # cannot be dropped without an explicit data migration.
    table = sa.table('question_bank', *(sa.column(n, sa.JSON()) for n in present))
    for row in bind.execute(sa.select(*(table.c[n] for n in present))):
        if any(value for value in row):
            raise RuntimeError('已保存评分元数据，拒绝删除；请先明确迁移策略。')
    with op.batch_alter_table('question_bank') as batch:
        for name in present:
            batch.drop_column(name)
