"""Actual Alembic/SQLite legacy migration preserves answer records."""
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import create_engine,text,inspect
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

def setup(tmp_path):
 backend=Path(__file__).resolve().parents[1];p=backend/'alembic/versions/202610101615_add_exam_submissions.py'
 spec=importlib.util.spec_from_file_location('exam_submission_fixture_migration',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 cfg=Config(str(backend/'alembic.ini'));cfg.set_main_option('script_location',str(backend/'alembic'))
 engine=create_engine('sqlite:///'+str(tmp_path/'migration.sqlite'))
 return module,cfg,engine

def old_tables(c):
 c.execute(text('PRAGMA foreign_keys=ON'))
 c.execute(text('CREATE TABLE exam_papers(id VARCHAR(36) PRIMARY KEY, title TEXT)'))
 c.execute(text('CREATE TABLE answer_records(id TEXT PRIMARY KEY, user_id VARCHAR(36), paper_id VARCHAR(36), user_answer TEXT)'))

def test_actual_upgrade_backfills_distinct_valid_legacy_and_preserves_orphans(tmp_path):
 module,cfg,engine=setup(tmp_path)
 try:
  with engine.begin() as c:
   old_tables(c);c.execute(text("INSERT INTO exam_papers VALUES('p','原卷')"))
   c.execute(text("INSERT INTO answer_records VALUES('a1','A','p','旧答案一'),('a2','A','p','旧答案二'),('a3','B','p','旧B'),('a4','A','gone','孤儿保留'),('a5','A',NULL,'交互保留')"))
   original=c.execute(text('SELECT * FROM answer_records ORDER BY id')).all();cfg.attributes['connection']=c;command.stamp(cfg,'202610101300');command.upgrade(cfg,'202610101615')
   assert c.execute(text('SELECT user_id,paper_id FROM exam_submissions ORDER BY user_id')).all()==[('A','p'),('B','p')]
   assert c.execute(text('SELECT * FROM answer_records ORDER BY id')).all()==original
   assert inspect(c).get_pk_constraint('exam_submissions')['constrained_columns']==['user_id','paper_id']
   assert inspect(c).get_foreign_keys('exam_submissions')[0]['options']['ondelete']=='CASCADE'
   with Operations.context(MigrationContext.configure(c)):module.upgrade()
   assert c.scalar(text('SELECT count(*) FROM exam_submissions'))==2
   with pytest.raises(RuntimeError,match='拒绝删除'):command.downgrade(cfg,'202610101300')
   assert c.scalar(text('SELECT count(*) FROM answer_records'))==5
   assert c.scalar(text('SELECT count(*) FROM exam_submissions'))==2
 finally:engine.dispose()

def test_actual_empty_upgrade_downgrade_can_run_again(tmp_path):
 module,cfg,engine=setup(tmp_path)
 try:
  with engine.begin() as c:
   old_tables(c);cfg.attributes['connection']=c;command.stamp(cfg,'202610101300');command.upgrade(cfg,'202610101615')
   command.downgrade(cfg,'202610101300');assert 'exam_submissions' not in inspect(c).get_table_names()
   command.upgrade(cfg,'202610101615');assert c.scalar(text('SELECT count(*) FROM exam_submissions'))==0
 finally:engine.dispose()
