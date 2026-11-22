"""New-table migration is idempotent and refuses to drop saved knowledge."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_new_collection_migration_preserves_existing_data():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/202610100930_add_knowledge_collections.py'
    spec = importlib.util.spec_from_file_location('collection_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT)'))
        conn.execute(sa.text("INSERT INTO documents VALUES ('old', 'existing-notes.md')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
            assert conn.scalar(sa.text('SELECT filename FROM documents')) == 'existing-notes.md'
            assert 'knowledge_collections' in sa.inspect(conn).get_table_names()
            conn.execute(sa.text("INSERT INTO knowledge_collections VALUES ('k','u','t','s','[]','{}','needs_review','2026-10-10')"))
            with pytest.raises(RuntimeError, match='拒绝删除'):
                migration.downgrade()
            assert conn.scalar(sa.text('SELECT COUNT(*) FROM knowledge_collections')) == 1
    engine.dispose()
