"""Native Alembic legacy schema upgrade, preservation and restart semantics."""
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text, inspect
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.core.cloud_workspaces import initialize_schema

REV='202610102130'; PREV='202610101854'
def config(c,owner=None):
    backend=Path(__file__).resolve().parents[1]
    cfg=Config(str(backend/'alembic.ini'));cfg.set_main_option('script_location',str(backend/'alembic'));cfg.attributes['connection']=c
    if owner is not None: cfg.attributes['legacy_conversation_owner']=owner
    return cfg

def legacy(c):
    c.execute(text('PRAGMA foreign_keys=ON'))
    c.execute(text('CREATE TABLE conversations(id VARCHAR(36) PRIMARY KEY,title TEXT,role_preset TEXT NOT NULL,created_at DATETIME)'))
    c.execute(text('CREATE TABLE messages(id VARCHAR(36) PRIMARY KEY,conversation_id VARCHAR(36) REFERENCES conversations(id),role TEXT,content TEXT)'))
    c.execute(text("INSERT INTO conversations VALUES('old','旧标题','tutor','2026-10-09 12:00:00')"))
    c.execute(text("INSERT INTO messages VALUES('m','old','assistant','旧中文回答')"))

@pytest.mark.parametrize('owner',[None,'cloud-alice'])
def test_upgrade_preserves_old_chat_and_assigns_explicit_legacy_owner(tmp_path,owner):
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.sqlite'))
    try:
        with engine.begin() as c:
            legacy(c); original=c.execute(text('SELECT * FROM messages')).all();cfg=config(c,owner)
            command.stamp(cfg,PREV);command.upgrade(cfg,REV);command.upgrade(cfg,REV)
            assert c.scalar(text('SELECT user_id FROM conversations'))==(owner or 'local_user')
            assert c.execute(text('SELECT * FROM messages')).all()==original
            assert c.scalar(text('SELECT title FROM conversations'))=='旧标题'
            assert not next(col for col in inspect(c).get_columns('conversations') if col['name']=='user_id')['nullable']
            assert any(i['column_names']==['user_id'] for i in inspect(c).get_indexes('conversations'))
    finally: engine.dispose()

def test_personal_cloud_initializer_routes_legacy_owner_without_guessing(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'cloud.sqlite'))
    try:
        with engine.begin() as c:
            legacy(c);command.stamp(config(c),PREV)
            initialize_schema(c,legacy_owner_id='cloud-owner')
            assert c.scalar(text('SELECT user_id FROM conversations'))=='cloud-owner'
    finally: engine.dispose()

def test_fresh_schema_default_and_nonnullable_account_owner(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'fresh.sqlite'))
    try:
        with engine.begin() as c:
            initialize_schema(c,legacy_owner_id='cloud-owner')
            c.execute(text("INSERT INTO conversations(id,title,role_preset) VALUES('raw','raw','tutor')"))
            assert c.scalar(text("SELECT user_id FROM conversations WHERE id='raw'"))=='local_user'
            assert c.scalar(text('SELECT version_num FROM alembic_version'))==ScriptDirectory.from_config(config(c)).get_current_head()
    finally: engine.dispose()

def test_interrupted_column_upgrade_does_not_claim_first_login_or_lose_owners(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'restart.sqlite'))
    try:
        with engine.begin() as c:
            legacy(c);c.execute(text("ALTER TABLE conversations ADD user_id VARCHAR(36) NOT NULL DEFAULT 'local_user'"))
            c.execute(text("UPDATE conversations SET user_id='previous-owner'"))
            command.stamp(config(c),PREV);command.upgrade(config(c,'new-owner'),REV)
            assert c.scalar(text('SELECT user_id FROM conversations'))=='previous-owner'
            cfg=config(c)
            with pytest.raises(RuntimeError,match='拒绝'):command.downgrade(cfg,PREV)
            assert c.scalar(text('SELECT content FROM messages'))=='旧中文回答'
    finally: engine.dispose()

def test_incompatible_preexisting_column_fails_before_stamping_head(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'bad.sqlite'))
    try:
        with engine.begin() as c:
            legacy(c);c.execute(text('ALTER TABLE conversations ADD user_id INTEGER'))
            cfg=config(c);command.stamp(cfg,PREV)
            with pytest.raises(RuntimeError,match='schema|结构'):command.upgrade(cfg,REV)
            assert c.scalar(text('SELECT version_num FROM alembic_version'))==PREV
            assert c.scalar(text('SELECT content FROM messages'))=='旧中文回答'
    finally:engine.dispose()

@pytest.mark.parametrize('owner',['', ' '*3, 'x'*37, 7])
def test_invalid_legacy_owner_preserves_unmodified_schema(tmp_path,owner):
    engine=create_engine('sqlite:///'+str(tmp_path/'invalid-owner.sqlite'))
    try:
        with engine.begin() as c:
            legacy(c);cfg=config(c,owner);command.stamp(cfg,PREV)
            with pytest.raises(RuntimeError,match='Invalid'):command.upgrade(cfg,REV)
            assert 'user_id' not in {col['name'] for col in inspect(c).get_columns('conversations')}
            assert c.scalar(text('SELECT content FROM messages'))=='旧中文回答'
    finally:engine.dispose()

def test_legacy_owner_is_sql_literal_and_reentry_keeps_initial_assignment(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'literal.sqlite'))
    try:
        with engine.begin() as c:
            legacy(c);cfg=config(c,"owner'quoted");command.stamp(cfg,PREV);command.upgrade(cfg,REV)
            assert c.scalar(text('SELECT user_id FROM conversations'))=="owner'quoted"
            command.stamp(cfg,PREV);command.upgrade(config(c,'second-owner'),REV)
            assert c.scalar(text('SELECT user_id FROM conversations'))=="owner'quoted"
    finally:engine.dispose()
