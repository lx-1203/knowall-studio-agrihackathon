"""Native Alembic migration against a real previous SQLite schema."""
import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

path=Path(__file__).resolve().parents[1]/'alembic/versions/202610091956_add_anki_content_versions.py'
spec=importlib.util.spec_from_file_location('versioned_anki_migration',path)
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)

@pytest.fixture
def old_database(tmp_path):
 engine=sa.create_engine('sqlite:///'+str(tmp_path/'old.sqlite'))
 with engine.begin() as conn:
  conn.execute(sa.text('CREATE TABLE flashcards(id VARCHAR(36) PRIMARY KEY, front TEXT NOT NULL, back TEXT NOT NULL)'))
  conn.execute(sa.text("INSERT INTO flashcards VALUES('old','旧问题','旧答案')"))
  with Operations.context(MigrationContext.configure(conn)):yield conn
 engine.dispose()

def test_upgrade_keeps_existing_content_and_is_idempotent(old_database):
 conn=old_database;migration.upgrade();migration.upgrade()
 assert conn.execute(sa.text('SELECT id,front,back,content_version,anki_export_version,anki_export_mtime FROM flashcards')).one()==('old','旧问题','旧答案',1,None,None)
 assert migration.down_revision=='202610091754'

def test_safe_downgrade_without_any_export_preserves_original_data(old_database):
 conn=old_database;migration.upgrade();migration.downgrade()
 assert {x['name'] for x in sa.inspect(conn).get_columns('flashcards')}=={'id','front','back'}
 assert conn.execute(sa.text('SELECT * FROM flashcards')).one()==('old','旧问题','旧答案')

def test_refuse_downgrade_that_loses_persisted_export_revision(old_database):
 conn=old_database;migration.upgrade();conn.execute(sa.text('UPDATE flashcards SET anki_export_version=1,anki_export_mtime=1790000000'))
 with pytest.raises(RuntimeError,match='拒绝删除'):migration.downgrade()
 assert conn.execute(sa.text('SELECT anki_export_mtime FROM flashcards')).scalar_one()==1790000000
