"""Validate only the new migration against a disposable database."""
import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from app.database import Base
import app.models


def test_study_pack_migration_preserves_existing_tables_and_rows():
    path = Path(__file__).parents[1] / 'alembic/versions/f1a5c9d3e7b2_add_study_packs.py'
    spec = importlib.util.spec_from_file_location('learning_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        Base.metadata.create_all(connection, tables=[table for name, table in Base.metadata.tables.items() if name != 'study_packs'])
        connection.execute(text("INSERT INTO documents (id, filename, file_type, sha256) VALUES ('keep', 'book.md', 'md', 'abc')"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            assert 'study_packs' in inspect(connection).get_table_names()
            assert len(inspect(connection).get_indexes('study_packs')) == 2
            assert connection.scalar(text('SELECT id FROM documents')) == 'keep'
            migration.downgrade()
            assert 'study_packs' not in inspect(connection).get_table_names()
            assert connection.scalar(text('SELECT id FROM documents')) == 'keep'
    engine.dispose()
