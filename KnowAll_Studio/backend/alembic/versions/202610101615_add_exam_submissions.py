"""Persist the ordinary-exam submission boundary without rewriting answers."""
from alembic import op
import sqlalchemy as sa

revision = '202610101615'
down_revision = '202610101300'
branch_labels = None
depends_on = None

def tables():
    metadata = sa.MetaData()
    papers = sa.Table('exam_papers', metadata, sa.Column('id', sa.String(36), primary_key=True))
    answers = sa.Table('answer_records', metadata, sa.Column('user_id', sa.String(36)), sa.Column('paper_id', sa.String(36)))
    submissions = sa.Table('exam_submissions', metadata,
        sa.Column('user_id', sa.String(36), primary_key=True),
        sa.Column('paper_id', sa.String(36), sa.ForeignKey('exam_papers.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    return papers, answers, submissions

def upgrade():
    papers, answers, submissions = tables()
    bind = op.get_bind()
    submissions.create(bind, checkfirst=True)
    # Legacy duplicates collapse only in the new marker table. Original answer
    # rows, including legacy orphan references, remain byte-for-byte unchanged.
    already = sa.exists(sa.select(1).where(
        submissions.c.user_id == answers.c.user_id,
        submissions.c.paper_id == answers.c.paper_id))
    legacy = sa.select(answers.c.user_id, answers.c.paper_id, sa.func.current_timestamp()).join(
        papers, papers.c.id == answers.c.paper_id).where(
        answers.c.user_id.is_not(None), ~already).distinct()
    bind.execute(submissions.insert().from_select(['user_id', 'paper_id', 'created_at'], legacy))

def downgrade():
    _, _, submissions = tables()
    bind = op.get_bind()
    if submissions.name not in sa.inspect(bind).get_table_names():
        return
    if bind.scalar(sa.select(sa.func.count()).select_from(submissions)):
        raise RuntimeError('已有试卷提交标记，拒绝删除；请先备份并迁移提交记录。')
    submissions.drop(bind)
