"""add glossary_terms table

Revision ID: ab12cd34ef56
Revises: f8a9b0c1d2e3
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "ab12cd34ef56"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("term", sa.String(256), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("full_explanation", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(64), nullable=False, server_default="综合"),
        sa.Column("chapter", sa.String(256), nullable=True),
        sa.Column("source_document_id", sa.String(36), nullable=True),
        sa.Column("source_node_id", sa.String(64), nullable=True),
        sa.Column("source_summary_id", sa.String(36), nullable=True),
        sa.Column("related_terms", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("subject", "term", name="uq_glossary_subject_term"),
    )
    op.create_index("ix_glossary_subject", "glossary_terms", ["subject"])
    op.create_index("ix_glossary_chapter", "glossary_terms", ["chapter"])


def downgrade():
    op.drop_index("ix_glossary_chapter", table_name="glossary_terms")
    op.drop_index("ix_glossary_subject", table_name="glossary_terms")
    op.drop_table("glossary_terms")
