"""Allow identical text chunks in different documents.

Revision ID: d6f3b9a4c1e2
Revises: c1d2e3f4a5b6
"""

from alembic import op
import sqlalchemy as sa


revision = "d6f3b9a4c1e2"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # On a new installation the initial migration has not created this table;
    # init_db() creates it from the model after migrations finish.
    if not inspector.has_table("document_chunks"):
        return

    unique = next(
        (constraint for constraint in inspector.get_unique_constraints("document_chunks")
         if constraint.get("column_names") == ["content_hash"]),
        None,
    )
    if unique is not None:
        if bind.dialect.name == "sqlite":
            # SQLite's column-level UNIQUE has no name. Batch mode assigns one
            # during reflection and recreates the table without that constraint.
            convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
            with op.batch_alter_table("document_chunks", naming_convention=convention) as batch:
                batch.drop_constraint(
                    unique["name"] or "uq_document_chunks_content_hash", type_="unique"
                )
        else:
            op.drop_constraint(unique["name"], "document_chunks", type_="unique")
        return

    # Some MySQL versions expose a UNIQUE column constraint as an index only.
    unique_index = next(
        (index for index in inspector.get_indexes("document_chunks")
         if index.get("unique") and index.get("column_names") == ["content_hash"]),
        None,
    )
    if unique_index is not None:
        op.drop_index(unique_index["name"], table_name="document_chunks")


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("document_chunks"):
        return
    # Existing duplicate hashes must be removed before downgrading.
    with op.batch_alter_table("document_chunks") as batch:
        batch.create_unique_constraint("uq_document_chunks_content_hash", ["content_hash"])
