"""add source provenance fields to glossary_terms

Revision ID: bc23de45ef67
Revises: ab12cd34ef56
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "bc23de45ef67"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "glossary_terms",
        sa.Column("source_type", sa.String(20), nullable=False, server_default="document"),
    )
    op.add_column(
        "glossary_terms",
        sa.Column("source_label", sa.String(512), nullable=True),
    )


def downgrade():
    op.drop_column("glossary_terms", "source_label")
    op.drop_column("glossary_terms", "source_type")
