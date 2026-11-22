"""Actual temporary Alembic migration and independent process persistence."""
import json
import os
from pathlib import Path
import subprocess
import sys
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine,inspect,text
from app.models import AgentInputCheckpoint

OUT=Path(__file__).resolve().parents[2]/'docs/open-source-integration/native-agent-input-checkpoint-2026-10-12/backend'


def cfg_for(conn):
    cfg=Config('alembic.ini');cfg.attributes['connection']=conn
    return cfg


@pytest.mark.parametrize('precreated',[False,True])
def test_actual_migration_preserves_legacy_run_and_four_column_control(tmp_path,precreated):
    engine=create_engine('sqlite:///'+(tmp_path/'migration.sqlite').as_posix())
    try:
        with engine.begin() as conn:
            cfg=cfg_for(conn)
            command.stamp(cfg,'202610102130');command.upgrade(cfg,'202610112250')
            conn.execute(text("INSERT INTO agent_runs (id,user_id,status,scope,model,sources,steps,created_at) VALUES ('legacy','alice','failed','readonly_documents','legacy','[]','[]','2026-10-11 00:00:00')"))
            if precreated:AgentInputCheckpoint.__table__.create(conn)
            command.upgrade(cfg,'head')
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='202610120015'
            assert {c['name'] for c in inspect(conn).get_columns('agent_background_tasks')}=={'run_id','user_id','cancel_requested','created_at'}
            assert {c['name'] for c in inspect(conn).get_columns('agent_input_checkpoints')}=={'run_id','user_id','version','input_ciphertext','created_at'}
            assert conn.execute(text('SELECT id FROM agent_runs')).scalar_one()=='legacy'
            command.upgrade(cfg,'head');command.downgrade(cfg,'202610112250')
            assert 'agent_input_checkpoints' not in inspect(conn).get_table_names()
            assert conn.execute(text('SELECT id FROM agent_runs')).scalar_one()=='legacy'
            assert 'agent_background_tasks' in inspect(conn).get_table_names()
            OUT.mkdir(parents=True,exist_ok=True)
            (OUT/f'migration-precreated-{precreated}.json').write_text(json.dumps({'database':'temporary SQLite',
                'upgraded_head':'202610120015','downgraded_head':'202610112250','legacy_preserved':True,
                'base_create_all_precreated':precreated,'paid_calls':0}),encoding='utf8')
    finally:engine.dispose()


@pytest.mark.parametrize('invalid', ['missing','wrong-type','nullable','wrong-pk','wrong-length'])
def test_existing_incompatible_schema_is_rejected_without_rewriting(tmp_path,invalid):
    engine=create_engine('sqlite:///'+(tmp_path/'incompatible.sqlite').as_posix())
    fields={'run_id':'VARCHAR(36) NOT NULL PRIMARY KEY','user_id':'VARCHAR(36) NOT NULL',
        'version':'INTEGER NOT NULL','input_ciphertext':'TEXT NOT NULL','created_at':'DATETIME NOT NULL'}
    if invalid=='missing':del fields['input_ciphertext']
    if invalid=='wrong-type':fields['input_ciphertext']='INTEGER NOT NULL'
    if invalid=='nullable':fields['user_id']='VARCHAR(36)'
    if invalid=='wrong-pk':fields['run_id']='VARCHAR(36) NOT NULL';fields['user_id']='VARCHAR(36) NOT NULL PRIMARY KEY'
    if invalid=='wrong-length':fields['run_id']='VARCHAR(12) NOT NULL PRIMARY KEY'
    try:
        with engine.begin() as conn:
            cfg=cfg_for(conn);command.stamp(cfg,'202610112250')
            conn.execute(text('CREATE TABLE agent_input_checkpoints ('+','.join(name+' '+kind for name,kind in fields.items())+')'))
            before=conn.execute(text("SELECT sql FROM sqlite_master WHERE name='agent_input_checkpoints'")).scalar_one()
            with pytest.raises(RuntimeError,match='incompatible'):command.upgrade(cfg,'head')
            assert conn.execute(text("SELECT sql FROM sqlite_master WHERE name='agent_input_checkpoints'")).scalar_one()==before
    finally:engine.dispose()


def test_two_independent_processes_decrypt_same_persisted_input_without_resume(tmp_path):
    helper=Path(__file__).with_name('agent_input_checkpoint_process_helper.py')
    sql=tmp_path/'cross-process.sqlite';key=tmp_path/'private-key'
    rows=[]
    for mode in ('write','read'):
        completed=subprocess.run([sys.executable,str(helper),mode,str(sql),str(key)],capture_output=True,
            text=True,encoding='utf8',timeout=60,env={**os.environ,'PYTHONIOENCODING':'utf8'})
        assert completed.returncode==0,completed.stderr
        rows.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert rows[0]['pid']!=rows[1]['pid'] and rows[0]['run_id']==rows[1]['run_id']
    assert rows[1]['message']=='cross-process checkpoint' and rows[1]['status']=='running'
    assert rows[1]['history']==[{'role':'user','content':'bounded prior text'}]
    assert rows[1]['execution_resumed'] is False and all(row['paid_calls']==0 for row in rows)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'two-process.json').write_text(json.dumps({'processes':rows,'database':'temporary SQLite',
        'key':'private temporary file','paid_calls':0},indent=2),encoding='utf8')
