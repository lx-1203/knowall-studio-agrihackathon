"""Add FSRS provenance and actual recitation review timestamps."""
from alembic import op
import sqlalchemy as sa
revision = '202610090423'
down_revision = 'f1a5c9d3e7b2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('review_schedule', sa.Column('fsrs_version', sa.String(32), nullable=True))
    op.add_column('recitation_records', sa.Column('fsrs_version', sa.String(32), nullable=True))
    op.add_column('recitation_records', sa.Column('last_review_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('recitation_records', 'last_review_at')
    op.drop_column('recitation_records', 'fsrs_version')
    op.drop_column('review_schedule', 'fsrs_version')
