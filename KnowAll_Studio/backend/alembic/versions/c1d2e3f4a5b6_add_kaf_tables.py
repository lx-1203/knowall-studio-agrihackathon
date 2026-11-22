"""add kaf_files and kaf_grants tables

Revision ID: c1d2e3f4a5b6
Revises: ab12cd34ef56
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4a5b6"
down_revision = "bc23de45ef67"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kaf_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_id", sa.String(64), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("node_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("source_doc_id", sa.String(36), nullable=True),
        sa.Column("source_doc_filename", sa.String(500), nullable=True, server_default=""),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=True, server_default="1"),
        sa.Column("enabled_tiers", sa.String(200), nullable=True,
                  server_default="free,pro,enterprise"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_kaf_files_file_id", "kaf_files", ["file_id"], unique=True)

    op.create_table(
        "kaf_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tier", sa.String(20), nullable=True, server_default="free"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_kaf_grants_file_id", "kaf_grants", ["file_id"])
    op.create_index("ix_kaf_grants_user_id", "kaf_grants", ["user_id"])


def downgrade():
    op.drop_index("ix_kaf_grants_user_id", table_name="kaf_grants")
    op.drop_index("ix_kaf_grants_file_id", table_name="kaf_grants")
    op.drop_table("kaf_grants")
    op.drop_index("ix_kaf_files_file_id", table_name="kaf_files")
    op.drop_table("kaf_files")
