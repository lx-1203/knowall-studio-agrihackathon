"""Real disposable DB + signed accounts, scheduling needs no external AI."""
import asyncio
import runpy
import json
import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect, text, select, delete, event, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.api import study
from app.database import get_db, Base
from app.models import StudyPlan, KnowledgePointNode, StudyReminder
from tests.test_study_notification_flow import flow, reminder, create, detail
from tests.test_enhanced_study_flow import enhanced, generate
from tests.test_pipeline_lifecycle_flow import localhost


def schedule_dates(result):
    return [day['date'] for day in result['short_term_plan'] or []] + [
        day['date'] for week in result['long_term_plan'] or [] for day in week['days']]


@pytest.mark.parametrize('kind', ['short', 'long', 'both'])
async def test_saved_schedule_matches_original_and_actual_end(enhanced, kind):
    response = await generate(enhanced, plan_type=kind, daily_hours=1.5)
    assert response.status_code == 200, response.text
    result = response.json()['result']
    saved = await detail(enhanced, result['plan_id'])
    assert saved.get('short_term_plan') == result['short_term_plan']
    assert saved.get('long_term_plan') == result['long_term_plan']
    assert saved['target_end_date'] == max(schedule_dates(result)) + 'T00:00:00'


async def test_normal_plan_reports_no_historical_schedule(flow):
    plan = await create(flow)
    saved = await detail(flow, plan)
    assert 'short_term_plan' in saved and saved['short_term_plan'] is None
    assert 'long_term_plan' in saved and saved['long_term_plan'] is None


@pytest.mark.parametrize('total', [1, 4, 5, 20, 60])
async def test_schedule_deadline_matches_actual_duration_across_month(enhanced, total):
    async with enhanced.factory() as session:
        await session.execute(delete(KnowledgePointNode))
        for index in range(total):
            session.add(KnowledgePointNode(id=f'new-{index}', summary_id='summary', document_id='source', level=2, sequence=index, title=f'知识点{index}', explanation='临时测试说明'))
        await session.commit()
    response = await generate(enhanced, plan_type='both', start_date='2026-10-31T18:00:00', daily_hours=2.5)
    assert response.status_code == 200, response.text
    result = response.json()['result']
    saved = await detail(enhanced, result['plan_id'])
    assert saved['target_end_date'] == max(schedule_dates(result)) + 'T00:00:00'
    assert saved['short_term_plan'] == result['short_term_plan']
    assert saved['long_term_plan'] == result['long_term_plan']
    assert saved['short_term_plan'][0]['hours'][-1]['hour'] == '10:00-10:30'


async def test_later_node_and_plan_changes_do_not_recompute_original_schedule(enhanced):
    response = await generate(enhanced, plan_type='both')
    result = response.json()['result']
    plan = result['plan_id']
    async with enhanced.factory() as session:
        await session.execute(delete(KnowledgePointNode))
        await session.commit()
    changed = await enhanced.client.put('/api/v1/study/plans/' + plan, headers=enhanced.alice, json={'name':'改名后的原计划', 'status':'paused'})
    assert changed.status_code == 200
    added = await enhanced.client.post('/api/v1/study/goals', headers=enhanced.alice, json={'plan_id':plan, 'title':'额外目标'})
    assert added.status_code == 200
    assert (await enhanced.client.put('/api/v1/study/goals/' + added.json()['goal_id'] + '/toggle', headers=enhanced.alice)).status_code == 200
    saved = await detail(enhanced, plan)
    assert saved['name'] == '改名后的原计划'
    assert saved['short_term_plan'] == result['short_term_plan']
    assert saved['long_term_plan'] == result['long_term_plan']


async def test_failed_schedule_commit_leaves_no_partial_plan(enhanced):
    engine = enhanced.factory.kw['bind']
    def fail(conn, cursor, statement, params, context, many):
        if statement.lower().startswith('insert into study_plans'):
            raise RuntimeError('simulated isolated write failure')
    event.listen(engine.sync_engine, 'before_cursor_execute', fail)
    try:
        response = await generate(enhanced)
        assert response.status_code == 500
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', fail)
    async with enhanced.factory() as session:
        assert await session.scalar(select(func.count()).select_from(StudyPlan)) == 0


async def test_fresh_python_process_reads_same_persisted_schedule(enhanced):
    result = (await generate(enhanced, plan_type='both')).json()['result']
    engine = enhanced.factory.kw['bind']
    url = engine.url.render_as_string(hide_password=False)
    await engine.dispose()
    code = '''
import asyncio, json, sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import StudyPlan
async def main():
    engine = create_async_engine(sys.argv[1])
    try:
        async with async_sessionmaker(engine)() as session:
            plan = await session.get(StudyPlan, sys.argv[2])
            print(json.dumps({'short_term_plan':plan.short_term_plan,'long_term_plan':plan.long_term_plan,'user_id':plan.user_id}))
    finally:
        await engine.dispose()
asyncio.run(main())
'''
    process = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, url, result['plan_id']], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert process.returncode == 0, process.stderr
    saved = json.loads(process.stdout)
    assert saved['user_id'] == 'alice'
    assert saved['short_term_plan'] == result['short_term_plan']
    assert saved['long_term_plan'] == result['long_term_plan']


async def test_real_http_reconnect_reads_saved_schedule_and_reminders(enhanced, monkeypatch):
    response = await generate(enhanced, plan_type='both')
    result = response.json()['result']
    rid = await reminder(enhanced, repeat_daily=True)
    old_engine = enhanced.factory.kw['bind']
    url = old_engine.url
    await old_engine.dispose()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(study.router)
    async def db():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    try:
        async with localhost(app) as client:
            saved = await client.get('/api/v1/study/plans/' + result['plan_id'], headers=enhanced.alice)
            assert saved.status_code == 200, saved.text
            assert saved.json().get('short_term_plan') == result['short_term_plan']
            assert saved.json().get('long_term_plan') == result['long_term_plan']
            foreign = await client.get('/api/v1/study/plans/' + result['plan_id'], headers=enhanced.bob)
            assert foreign.status_code == 404
            due = await client.get('/api/v1/study/reminders/due', headers=enhanced.alice)
            assert [row['id'] for row in due.json()['reminders']] == [rid]
            responses = await asyncio.gather(*[client.post('/api/v1/study/reminders/' + rid + '/read', headers=enhanced.alice) for _ in range(8)])
            assert all(item.status_code == 200 for item in responses), [item.text for item in responses]
            assert (await client.get('/api/v1/study/reminders/due', headers=enhanced.alice)).json()['count'] == 0
        async with factory() as session:
            row = await session.get(StudyReminder, rid)
            assert row.remind_at == datetime(2026, 10, 9, 23) and not row.is_read
    finally:
        await engine.dispose()


@pytest.mark.parametrize('repeat', [False, True])
async def test_concurrent_confirmation_is_idempotent_and_owned(flow, repeat):
    rid = await reminder(flow, repeat_daily=repeat)
    responses = await asyncio.gather(*[flow.client.post('/api/v1/study/reminders/' + rid + '/read', headers=flow.alice) for _ in range(8)])
    assert all(response.status_code == 200 for response in responses)
    assert (await flow.client.post('/api/v1/study/reminders/' + rid + '/read', headers=flow.bob)).status_code == 404
    assert (await flow.client.get('/api/v1/study/reminders/due', headers=flow.alice)).json()['count'] == 0
    async with flow.factory() as session:
        saved = await session.get(StudyReminder, rid)
        assert saved.is_read == (not repeat)
        assert saved.remind_at == datetime(2026, 10, 9 if repeat else 8, 23)


async def test_future_daily_confirmation_does_not_move_schedule(flow):
    rid = await reminder(flow, repeat_daily=True, remind_at='2026-10-11T00:00:00Z')
    responses = await asyncio.gather(*[flow.client.post('/api/v1/study/reminders/' + rid + '/read', headers=flow.alice) for _ in range(8)])
    assert all(response.status_code == 200 for response in responses)
    async with flow.factory() as session:
        saved = await session.get(StudyReminder, rid)
        assert saved.remind_at == datetime(2026, 10, 11) and not saved.is_read


def migration():
    return runpy.run_path(str(Path(__file__).parents[1] / 'alembic/versions/202610090850_add_study_plan_schedules.py'))


def test_upgrade_preserves_old_rows_is_repeatable_and_reversible(tmp_path):
    upgrade = migration()
    engine = create_engine('sqlite:///' + (tmp_path / 'legacy.sqlite').as_posix())
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE study_plans (id TEXT PRIMARY KEY, name TEXT, progress FLOAT)'))
            conn.execute(text("INSERT INTO study_plans VALUES ('legacy', '原有计划', 50)"))
            with Operations.context(MigrationContext.configure(conn)):
                upgrade['upgrade']()
                upgrade['upgrade']()
                assert conn.execute(text('SELECT name, progress, short_term_plan, long_term_plan FROM study_plans')).one() == ('原有计划', 50, None, None)
                upgrade['downgrade']()
                assert conn.execute(text('SELECT * FROM study_plans')).one() == ('legacy', '原有计划', 50)
                upgrade['upgrade']()
            assert len(inspect(conn).get_columns('study_plans')) == 5
    finally:
        engine.dispose()


def test_upgrade_handles_absent_table_without_claiming_creation(tmp_path):
    upgrade = migration()
    engine = create_engine('sqlite:///' + (tmp_path / 'empty.sqlite').as_posix())
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            upgrade['upgrade']()
            upgrade['downgrade']()
            assert 'study_plans' not in inspect(conn).get_table_names()
    finally:
        engine.dispose()


def current_migration_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = Path(__file__).parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    return ScriptDirectory.from_config(config).get_current_head()


def test_real_alembic_upgrade_from_current_head_preserves_model_compatibility(tmp_path):
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    engine = create_engine('sqlite:///' + (tmp_path / 'upgrade.sqlite').as_posix())
    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn, tables=[StudyPlan.__table__])
            conn.execute(text('ALTER TABLE study_plans DROP COLUMN short_term_plan'))
            conn.execute(text('ALTER TABLE study_plans DROP COLUMN long_term_plan'))
            conn.execute(text("INSERT INTO study_plans (id,user_id,name,progress) VALUES ('old','alice','原计划',25)"))
            config.attributes['connection'] = conn
            command.stamp(config, '202610090423')
            command.upgrade(config, 'head')
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == current_migration_head()
            assert conn.execute(select(StudyPlan.name, StudyPlan.progress, StudyPlan.short_term_plan, StudyPlan.long_term_plan)).one() == ('原计划', 25, None, None)
            command.upgrade(config, 'head')
            command.downgrade(config, '202610090423')
            assert conn.execute(text('SELECT name,progress FROM study_plans')).one() == ('原计划', 25)
            command.upgrade(config, 'head')
            assert conn.execute(select(StudyPlan.short_term_plan)).scalar_one() is None
    finally:
        engine.dispose()


@pytest.mark.parametrize('legacy', [False, True])
async def test_actual_init_db_in_isolated_process_preserves_data(tmp_path, legacy):
    """Run the real startup migration with debug=False, only against temp DB."""
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).parents[1]
    path = tmp_path / 'startup.sqlite'
    if legacy:
        engine = create_engine('sqlite:///' + path.as_posix())
        config = Config(str(root / 'alembic.ini'))
        config.set_main_option('script_location', str(root / 'alembic'))
        try:
            with engine.begin() as conn:
                Base.metadata.create_all(conn)
                conn.execute(text('ALTER TABLE study_plans DROP COLUMN short_term_plan'))
                conn.execute(text('ALTER TABLE study_plans DROP COLUMN long_term_plan'))
                conn.execute(text("INSERT INTO study_plans (id,user_id,name,progress) VALUES ('old','alice','legacy-plan',25)"))
                config.attributes['connection'] = conn
                command.stamp(config, '202610090423')
        finally:
            engine.dispose()
    env = os.environ.copy()
    env.update(DATABASE_URL='sqlite+aiosqlite:///' + path.as_posix(), DEBUG='false',
               KNOWALL_DATA_ROOT=str(tmp_path / 'data'), KNOWALL_LOG_DIR=str(tmp_path / 'logs'),
               CHROMA_PERSIST_DIR=str(tmp_path / 'vectors'), DOCUMENT_DIR=str(tmp_path / 'documents'),
               EXPORT_DIR=str(tmp_path / 'exports'), GRAPHRAG_ENABLED='false',
               KNOWALL_IGNORE_ENV_FILE='1', JWT_SECRET='isolated-round06b3-test-secret-only', PYTHONIOENCODING='utf-8')
    code = '''
import asyncio, json
from app.database import init_db, engine, async_session
from app.models import StudyPlan
from sqlalchemy import text
async def main():
    try:
        await init_db()
        await init_db()
        async with async_session() as session:
            plan = await session.get(StudyPlan, 'old')
            print(json.dumps({'head':await session.scalar(text('SELECT version_num FROM alembic_version')),
                              'plan':None if plan is None else {'name':plan.name,'progress':plan.progress,'short':plan.short_term_plan,'long':plan.long_term_plan}}))
    finally:
        await engine.dispose()
asyncio.run(main())
'''
    process = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert result['head'] == current_migration_head()
    assert result['plan'] == ({'name':'legacy-plan', 'progress':25, 'short':None, 'long':None} if legacy else None)


async def test_failed_existing_upgrade_keeps_rows_and_exposes_error(tmp_path):
    root = Path(__file__).parents[1]
    path = tmp_path / 'unknown.sqlite'
    engine = create_engine('sqlite:///' + path.as_posix())
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE sentinel (content TEXT)'))
            conn.execute(text("INSERT INTO sentinel VALUES ('preserve-existing-data')"))
    finally:
        engine.dispose()
    env = os.environ.copy()
    env.update(DATABASE_URL='sqlite+aiosqlite:///' + path.as_posix(), DEBUG='false',
               KNOWALL_DATA_ROOT=str(tmp_path / 'data'), PYTHONIOENCODING='utf-8',
               JWT_SECRET='isolated-round06b3-test-secret-only')
    code = '''
import asyncio, json
from app.database import init_db, engine, async_session
from sqlalchemy import text
async def main():
    try:
        try:
            await init_db()
        except RuntimeError as error:
            async with async_session() as session:
                print(json.dumps({'error':str(error),'row':await session.scalar(text('SELECT content FROM sentinel'))}))
        else:
            raise AssertionError('An unknown nonempty DB must not be treated as a fresh install')
    finally:
        await engine.dispose()
asyncio.run(main())
'''
    process = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert '数据库迁移失败' in result['error']
    assert result['row'] == 'preserve-existing-data'


def test_cloud_workprocess_upgrade_keeps_legacy_plan_and_loads_new_schedule_columns(tmp_path):
    from app.core.cloud_workspaces import initialize_schema
    engine = create_engine('sqlite:///' + (tmp_path / 'cloud.sqlite').as_posix())
    try:
        with engine.begin() as conn:
            initialize_schema(conn)
            conn.execute(text('ALTER TABLE study_plans DROP COLUMN short_term_plan'))
            conn.execute(text('ALTER TABLE study_plans DROP COLUMN long_term_plan'))
            conn.execute(text("UPDATE alembic_version SET version_num='202610090423'"))
            conn.execute(text("INSERT INTO study_plans (id,user_id,name,progress) VALUES ('keep','alice','原有云计划',75)"))
            initialize_schema(conn)
            assert conn.scalar(text('SELECT version_num FROM alembic_version')) == current_migration_head()
            assert conn.execute(select(StudyPlan.name, StudyPlan.progress, StudyPlan.short_term_plan, StudyPlan.long_term_plan)).one() == ('原有云计划', 75, None, None)
    finally:
        engine.dispose()
