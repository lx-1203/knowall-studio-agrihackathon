"""Native Alembic operations preserve a real cached old export."""
import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

path=Path(__file__).resolve().parents[1]/'alembic/versions/202610092122_add_anki_export_format.py'
spec=importlib.util.spec_from_file_location('anki_format_migration',path)
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)


@pytest.fixture
def old_database(tmp_path):
    engine=sa.create_engine('sqlite:///'+str(tmp_path/'old.sqlite'))
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE flashcards(id TEXT PRIMARY KEY, front TEXT, back TEXT, content_version INTEGER, anki_export_version INTEGER, anki_export_mtime BIGINT)'))
        conn.execute(sa.text("INSERT INTO flashcards VALUES('old','问题','$x_1$',1,1,1790000000)"))
        with Operations.context(MigrationContext.configure(conn)):yield conn
    engine.dispose()


def test_upgrade_preserves_cached_export_and_is_idempotent(old_database):
    conn=old_database;migration.upgrade();migration.upgrade()
    assert migration.down_revision=='202610091956'
    assert conn.execute(sa.text('SELECT * FROM flashcards')).one()==('old','问题','$x_1$',1,1,1790000000,None)


def test_unused_format_column_downgrade_preserves_old_data(old_database):
    conn=old_database;migration.upgrade();migration.downgrade()
    assert conn.execute(sa.text('SELECT * FROM flashcards')).one()==('old','问题','$x_1$',1,1,1790000000)


def test_used_format_revision_cannot_be_silently_lost(old_database):
    conn=old_database;migration.upgrade()
    conn.execute(sa.text('UPDATE flashcards SET anki_export_format=2'))
    with pytest.raises(RuntimeError,match='拒绝删除'):migration.downgrade()
    assert conn.execute(sa.text('SELECT anki_export_format FROM flashcards')).scalar_one()==2
