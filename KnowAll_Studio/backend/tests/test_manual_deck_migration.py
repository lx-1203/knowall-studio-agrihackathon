"""Native Alembic upgrade/downgrade on owned temporary old schemas."""
from pathlib import Path
import importlib.util
import pytest,sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mysql

root=Path(__file__).resolve().parents[1]
revision=root/'alembic/versions/202610091754_add_manual_deck_owner.py'

@pytest.fixture
def old(tmp_path):
 engine=sa.create_engine('sqlite:///'+(tmp_path/'old.sqlite').as_posix())
 with engine.begin() as c:
  c.exec_driver_sql('CREATE TABLE decks (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, description TEXT, card_count INTEGER, created_at DATETIME)')
  c.exec_driver_sql("INSERT INTO decks VALUES ('legacy','旧共享牌组','原始描述',1,'2026-01-01')")
  c.exec_driver_sql('CREATE TABLE flashcards (id VARCHAR(36) PRIMARY KEY, deck_id VARCHAR(36), front TEXT)')
  c.exec_driver_sql("INSERT INTO flashcards VALUES ('card','legacy','原始卡片')")
  cfg=Config(str(root/'alembic.ini'));cfg.set_main_option('script_location',str(root/'alembic'));cfg.attributes['connection']=c
  command.stamp(cfg,'202610090850')
  yield c,cfg
 engine.dispose()

def module():
 assert revision.is_file(),'New additive owner migration is missing'
 spec=importlib.util.spec_from_file_location('manual_owner',revision);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_upgrade_old_data_preserved_and_idempotent(old):
 c,cfg=old;m=module();command.upgrade(cfg,'head')
 assert 'user_id' in {x['name'] for x in sa.inspect(c).get_columns('decks')}
 assert c.exec_driver_sql('SELECT name,card_count,user_id FROM decks').one()==('旧共享牌组',1,None)
 assert c.exec_driver_sql('SELECT front FROM flashcards').scalar()=='原始卡片'
 with Operations.context(MigrationContext.configure(c)):m.upgrade()
 assert [i['name'] for i in sa.inspect(c).get_indexes('decks')].count('ix_decks_user_id')==1

def test_nullable_legacy_downgrade_preserves_cards(old):
 c,cfg=old;module();command.upgrade(cfg,'head');command.downgrade(cfg,'202610090850')
 assert 'user_id' not in {x['name'] for x in sa.inspect(c).get_columns('decks')}
 assert c.exec_driver_sql('SELECT front FROM flashcards').scalar()=='原始卡片'

def test_owned_downgrade_refuses_privacy_loss(old):
 c,cfg=old;module();command.upgrade(cfg,'head');c.exec_driver_sql("UPDATE decks SET user_id='A'")
 with pytest.raises(RuntimeError,match='私有牌组'):command.downgrade(cfg,'202610090850')
 assert c.exec_driver_sql('SELECT user_id FROM decks').scalar()=='A'

def test_mysql_native_column_and_index_compile():
 module()
 table=sa.Table('decks',sa.MetaData(),sa.Column('user_id',sa.String(36),nullable=True))
 assert 'VARCHAR(36)' in str(sa.schema.CreateColumn(table.c.user_id).compile(dialect=mysql.dialect()))
 assert 'ix_decks_user_id' in str(sa.schema.CreateIndex(sa.Index('ix_decks_user_id',table.c.user_id)).compile(dialect=mysql.dialect()))
