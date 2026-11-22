"""Add an explicit administrator role to existing users.

Revision ID: e2a4c6b8d0f1
Revises: d6f3b9a4c1e2
"""

from alembic import op
import sqlalchemy as sa


revision = "e2a4c6b8d0f1"
down_revision = "d6f3b9a4c1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The server default makes every existing account non-admin on upgrade.
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_admin")
