"""Native MySQL 8 private schema; never connects to a configured product DB."""
import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine,text,inspect
from alembic import command
from alembic.config import Config

@pytest.mark.parametrize('owner',['local_user','cloud-owner'])
def test_mysql_native_old_schema_owner_default_and_restart(owner):
    url=os.environ.get('KNOWALL_ROUND53_MYSQL_URL')
    if not url:pytest.skip('Round53 private MySQL runner not started')
    engine=create_engine(url)
    try:
        with engine.begin() as c:
            for table in ['messages','conversations','alembic_version']:c.execute(text('DROP TABLE IF EXISTS '+table))
            c.execute(text('CREATE TABLE conversations(id VARCHAR(36) PRIMARY KEY,title VARCHAR(200),role_preset VARCHAR(50) NOT NULL,created_at DATETIME)'))
            c.execute(text('CREATE TABLE messages(id VARCHAR(36) PRIMARY KEY,conversation_id VARCHAR(36),content TEXT, FOREIGN KEY(conversation_id) REFERENCES conversations(id))'))
            c.execute(text("INSERT INTO conversations VALUES('old','旧标题','tutor','2026-10-09 12:00:00')"))
            c.execute(text("INSERT INTO messages VALUES('m','old','旧消息')"))
            original=c.execute(text('SELECT * FROM messages')).all()
            backend=Path(__file__).resolve().parents[1];cfg=Config(str(backend/'alembic.ini'));cfg.set_main_option('script_location',str(backend/'alembic'));cfg.attributes.update(connection=c,legacy_conversation_owner=owner)
            command.stamp(cfg,'202610101854');command.upgrade(cfg,'202610102130')
            assert c.scalar(text('SELECT user_id FROM conversations'))==owner
            assert c.execute(text('SELECT * FROM messages')).all()==original
            assert any(i['column_names']==['user_id'] for i in inspect(c).get_indexes('conversations'))
            cfg.attributes['legacy_conversation_owner']='second-owner';command.stamp(cfg,'202610101854');command.upgrade(cfg,'202610102130')
            assert c.scalar(text('SELECT user_id FROM conversations'))==owner
            if owner!='local_user':
                with pytest.raises(RuntimeError,match='拒绝'):command.downgrade(cfg,'202610101854')
                assert c.scalar(text('SELECT content FROM messages'))=='旧消息'
    finally:engine.dispose()
