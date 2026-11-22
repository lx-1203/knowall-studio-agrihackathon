"""Existing databases must accept repeated chunk text after upgrading."""

import runpy
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_remove_chunk_hash_uniqueness_preserves_existing_rows(tmp_path):
    metadata = sa.MetaData()
    documents = sa.Table(
        "documents", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    chunks = sa.Table(
        "document_chunks", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doc_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), unique=True),
        sa.Column("text_content", sa.Text(), nullable=False),
    )
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    migration = runpy.run_path(str(
        Path(__file__).resolve().parents[1]
        / "alembic/versions/d6f3b9a4c1e2_allow_repeated_document_chunks.py"
    ))
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(documents.insert(), [{"id": "doc-1"}, {"id": "doc-2"}])
            connection.execute(chunks.insert(), {
                "id": "chunk-1", "doc_id": "doc-1", "content_hash": "same",
                "text_content": "shared text",
            })

        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration["upgrade"]()

        with engine.begin() as connection:
            connection.execute(chunks.insert(), {
                "id": "chunk-2", "doc_id": "doc-2", "content_hash": "same",
                "text_content": "shared text",
            })
            rows = connection.execute(
                sa.select(chunks.c.id, chunks.c.doc_id).order_by(chunks.c.id)
            ).all()

        assert rows == [("chunk-1", "doc-1"), ("chunk-2", "doc-2")]
        assert not any(
            constraint["column_names"] == ["content_hash"]
            for constraint in sa.inspect(engine).get_unique_constraints("document_chunks")
        )
    finally:
        engine.dispose()


def test_migration_allows_new_install_before_tables_exist(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    migration = runpy.run_path(str(
        Path(__file__).resolve().parents[1]
        / "alembic/versions/d6f3b9a4c1e2_allow_repeated_document_chunks.py"
    ))
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration["upgrade"]()
        assert not sa.inspect(engine).has_table("document_chunks")
    finally:
        engine.dispose()
