"""Real temporary SQLite and signed accounts; AI output is explicitly simulated."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select, func, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import study, notifications
from app.core.auth import create_access_token
from app.database import Base, get_db
from app.models import StudyPlan, StudyGoal, StudyReminder, Document, KnowledgeSummary, KnowledgePointNode, APIKey
from app.models.user import User
from app.models.notification import Notification


@pytest.fixture
async def flow(tmp_path, monkeypatch):
    import app.database
    from app.core.assistant import assistant
    engine=create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'study.sqlite').as_posix())
    @event.listens_for(engine.sync_engine, 'connect')
    def enforce_foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[User.__table__, APIKey.__table__, StudyPlan.__table__, StudyGoal.__table__, StudyReminder.__table__, Notification.__table__, Document.__table__, KnowledgeSummary.__table__, KnowledgePointNode.__table__])
    factory=async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, 'async_session', factory)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value=cls(2026,10,9,0,0,tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(study, 'datetime', Clock)
    async with factory() as db:
        for name in ('alice','bob'):
            db.add(User(id=name, username=name, email=name+'@example.invalid', password_hash='unused'))
        await db.flush()
        db.add(Document(id='source', filename='biology.txt', file_type='txt', sha256='isolated'))
        await db.flush()
        db.add(KnowledgeSummary(id='summary', document_id='source', content_md='# 细胞\n## 能量'))
        db.add(Notification(id='na', user_id='alice', title='Alice学习通知', category='study'))
        db.add(Notification(id='nb', user_id='bob', title='Bob私有通知', category='study'))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    app=FastAPI()
    app.include_router(study.router)
    app.include_router(notifications.router)
    app.dependency_overrides[get_db]=database
    model=AsyncMock(return_value=json.dumps({'name':'模拟计划','goals':[{'title':'细胞目标','priority':'high'}]}))
    monkeypatch.setattr(assistant, 'chat', model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://study') as client:
        yield SimpleNamespace(client=client, factory=factory, model=model,
                              alice={'Authorization':'Bearer '+create_access_token('alice')},
                              bob={'Authorization':'Bearer '+create_access_token('bob')})
    await engine.dispose()


async def create(flow, goals=None, headers=None):
    response=await flow.client.post('/api/v1/study/plans', headers=headers or flow.alice,
                                   json={'name':'Alice计划','goals':goals or [{'title':'目标一'}]})
    assert response.status_code==200, response.text
    return response.json()['plan_id']


async def detail(flow, plan, headers=None):
    response=await flow.client.get('/api/v1/study/plans/'+plan, headers=headers or flow.alice)
    assert response.status_code==200, response.text
    return response.json()


async def test_create_read_and_reopen_completion_persists(flow):
    plan=await create(flow)
    goal=(await detail(flow,plan))['goals'][0]['id']
    assert (await flow.client.put('/api/v1/study/goals/'+goal+'/toggle',headers=flow.alice)).status_code==200
    assert (await detail(flow,plan))['status']=='completed'
    await flow.client.put('/api/v1/study/goals/'+goal+'/toggle',headers=flow.alice)
    data=await detail(flow,plan)
    assert data['status']=='active'
    assert data['progress']==0 and data['goals'][0]['completed_at'] is None


async def test_goal_append_order_and_progress_after_deletion(flow):
    plan=await create(flow)
    goal=(await detail(flow,plan))['goals'][0]['id']
    await flow.client.put('/api/v1/study/goals/'+goal+'/toggle',headers=flow.alice)
    added=await flow.client.post('/api/v1/study/goals',headers=flow.alice,json={'plan_id':plan,'title':'目标二'})
    assert added.status_code==200
    data=await detail(flow,plan)
    assert [g['order_index'] for g in data['goals']]==[0,1]
    assert data['progress']==50 and data['status']=='active'
    await flow.client.delete('/api/v1/study/goals/'+added.json()['goal_id'],headers=flow.alice)
    assert (await detail(flow,plan))['progress']==100
    await flow.client.delete('/api/v1/study/goals/'+goal,headers=flow.alice)
    data=await detail(flow,plan)
    assert data['progress']==0 and data['status']=='active'


async def test_plan_list_only_returns_current_account(flow):
    await create(flow)
    response=await flow.client.get('/api/v1/study/plans',headers=flow.bob)
    assert response.status_code==200
    assert response.json()==[]


@pytest.mark.parametrize('method,suffix,body', [('get','',None),('put','',{'name':'入侵'}),('delete','',None),('get','/ebbinghaus',None)])
async def test_foreign_plan_cannot_be_read_or_mutated(flow,method,suffix,body):
    plan=await create(flow)
    response=await flow.client.request(method,'/api/v1/study/plans/'+plan+suffix,headers=flow.bob,json=body)
    assert response.status_code==404, response.text
    assert (await detail(flow,plan))['name']=='Alice计划'


@pytest.mark.parametrize('action',['create','toggle','delete'])
async def test_foreign_goal_cannot_be_mutated(flow,action):
    plan=await create(flow)
    goal=(await detail(flow,plan))['goals'][0]['id']
    if action=='create':
        response=await flow.client.post('/api/v1/study/goals',headers=flow.bob,json={'plan_id':plan,'title':'不应创建'})
    elif action=='toggle':
        response=await flow.client.put('/api/v1/study/goals/'+goal+'/toggle',headers=flow.bob)
    else:
        response=await flow.client.delete('/api/v1/study/goals/'+goal,headers=flow.bob)
    assert response.status_code==404, response.text


async def test_invalid_token_does_not_fall_back_to_local_account(flow):
    response=await flow.client.post('/api/v1/study/plans',headers={'Authorization':'Bearer invalid'},json={'name':'不应创建'})
    assert response.status_code==401
    async with flow.factory() as db:
        assert (await db.scalar(select(func.count()).select_from(StudyPlan)))==0


@pytest.mark.parametrize('body',[{'name':' '},{'name':'p','goals':[{'title':' '}]},{'name':'p','goals':[{'title':'g','priority':'wrong'}]},{'name':'p','target_end_date':'not-date'}])
async def test_invalid_plan_is_not_saved(flow,body):
    response=await flow.client.post('/api/v1/study/plans',headers=flow.alice,json=body)
    assert response.status_code==422, response.text


@pytest.mark.parametrize('output',['{}','[]','{"name":"空计划","goals":[]}','{"name":"坏目标","goals":[{"title":" "}]}'])
async def test_invalid_model_plan_is_not_saved(flow,output):
    flow.model.return_value=output
    response=await flow.client.post('/api/v1/study/generate-plan',headers=flow.alice,json={'knowledge_text':'细胞能量'})
    assert response.status_code in (502,500), response.text
    async with flow.factory() as db:
        assert (await db.scalar(select(func.count()).select_from(StudyPlan)))==0


async def test_valid_simulated_generation_and_enhanced_plan_belong_to_caller(flow):
    generated=await flow.client.post('/api/v1/study/generate-plan',headers=flow.alice,json={'knowledge_text':'细胞能量'})
    assert generated.status_code==200, generated.text
    assert (await detail(flow,generated.json()['plan_id']))['goals'][0]['title']=='细胞目标'
    enhanced=await flow.client.post('/api/v1/study/generate-plan-enhanced',headers=flow.alice,json={'summary_id':'summary','start_date':'2026-10-10'})
    assert enhanced.status_code==200, enhanced.text
    async with flow.factory() as db:
        plans=(await db.execute(select(StudyPlan))).scalars().all()
        assert len(plans)==2 and all(p.user_id=='alice' for p in plans)


async def reminder(flow, **changes):
    body={'message':'学习提醒','remind_at':'2026-10-08T23:00:00Z',**changes}
    response=await flow.client.post('/api/v1/study/reminders',headers=flow.alice,json=body)
    assert response.status_code==200, response.text
    return response.json()['reminder_id']


async def test_reminder_offset_is_utc_and_future_is_not_due(flow):
    rid=await reminder(flow,remind_at='2026-10-09T08:30:00+08:00')
    async with flow.factory() as db:
        assert (await db.get(StudyReminder,rid)).remind_at==datetime(2026,10,9,0,30)
    assert (await flow.client.get('/api/v1/study/reminders/due',headers=flow.alice)).json()['count']==0


async def test_daily_reminder_advances_past_missed_days(flow):
    rid=await reminder(flow,repeat_daily=True,remind_at='2026-10-05T23:00:00Z')
    assert (await flow.client.get('/api/v1/study/reminders/due',headers=flow.alice)).json()['count']==1
    assert (await flow.client.post('/api/v1/study/reminders/'+rid+'/read',headers=flow.alice)).status_code==200
    assert (await flow.client.get('/api/v1/study/reminders/due',headers=flow.alice)).json()['count']==0
    async with flow.factory() as db:
        row=await db.get(StudyReminder,rid)
        assert row.remind_at==datetime(2026,10,9,23) and row.is_read is False


async def test_foreign_reminder_cannot_be_dismissed(flow):
    rid=await reminder(flow)
    assert (await flow.client.get('/api/v1/study/reminders/due',headers=flow.bob)).json()['count']==0
    response=await flow.client.post('/api/v1/study/reminders/'+rid+'/read',headers=flow.bob)
    assert response.status_code==404
    assert (await flow.client.get('/api/v1/study/reminders/due',headers=flow.alice)).json()['count']==1


async def test_reminder_association_rejects_foreign_or_mismatched_goals(flow):
    owned=await create(flow)
    foreign=await create(flow,headers=flow.bob)
    own_goal=(await detail(flow,owned))['goals'][0]['id']
    foreign_goal=(await detail(flow,foreign,flow.bob))['goals'][0]['id']
    for fields,status in [({'plan_id':foreign},404),({'goal_id':foreign_goal},404),({'plan_id':owned,'goal_id':foreign_goal},404),({'plan_id':foreign,'goal_id':own_goal},404)]:
        response=await flow.client.post('/api/v1/study/reminders',headers=flow.alice,json={'message':'不应关联','remind_at':'2026-10-08T23:00:00Z',**fields})
        assert response.status_code==status, response.text


async def test_delete_plan_removes_goals_and_goal_only_reminders(flow):
    plan=await create(flow)
    goal=(await detail(flow,plan))['goals'][0]['id']
    await reminder(flow,goal_id=goal)
    assert (await flow.client.delete('/api/v1/study/plans/'+plan,headers=flow.alice)).status_code==200
    async with flow.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyGoal))==0
        assert await db.scalar(select(func.count()).select_from(StudyReminder))==0


async def test_notification_filter_read_and_batch_delete_are_account_scoped(flow):
    data=(await flow.client.get('/api/v1/notifications',headers=flow.alice,params={'is_read':'0','category':'study'})).json()
    assert data['total']==1 and data['unread_count']==1 and data['items'][0]['id']=='na'
    assert (await flow.client.put('/api/v1/notifications/nb/read',headers=flow.alice)).status_code==404
    assert (await flow.client.put('/api/v1/notifications/read-all',headers=flow.alice)).status_code==200
    data=(await flow.client.get('/api/v1/notifications',headers=flow.alice)).json()
    assert data['unread_count']==0 and data['items'][0]['is_read'] is True
    assert (await flow.client.delete('/api/v1/notifications',headers=flow.alice,params={'ids':'na,nb'})).status_code==200
    data=(await flow.client.get('/api/v1/notifications',headers=flow.bob)).json()
    assert data['total']==1 and data['items'][0]['id']=='nb' and data['unread_count']==1


@pytest.mark.parametrize('body',[{'name':' '},{'progress':-1},{'status':'unknown'},{'name':None},{'progress':None}])
async def test_invalid_updates_do_not_corrupt_saved_plan(flow,body):
    plan=await create(flow)
    response=await flow.client.put('/api/v1/study/plans/'+plan,headers=flow.alice,json=body)
    assert response.status_code==422, response.text
    assert (await detail(flow,plan))['name']=='Alice计划'


async def test_target_date_can_be_cleared(flow):
    response=await flow.client.post('/api/v1/study/plans',headers=flow.alice,json={'name':'带截止日期','target_end_date':'2026-10-10T08:00:00+08:00'})
    plan=response.json()['plan_id']
    assert (await detail(flow,plan))['target_end_date']=='2026-10-10T00:00:00'
    await flow.client.put('/api/v1/study/plans/'+plan,headers=flow.alice,json={'target_end_date':None})
    assert (await detail(flow,plan))['target_end_date'] is None


async def test_duplicate_daily_dismiss_does_not_skip_an_extra_day(flow):
    rid=await reminder(flow,repeat_daily=True)
    for _ in range(2):
        await flow.client.post('/api/v1/study/reminders/'+rid+'/read',headers=flow.alice)
    async with flow.factory() as db:
        assert (await db.get(StudyReminder,rid)).remind_at==datetime(2026,10,9,23)


async def test_cleanup_also_works_without_sqlite_foreign_key_cascades(flow):
    plan=await create(flow)
    goal=(await detail(flow,plan))['goals'][0]['id']
    await reminder(flow,goal_id=goal)
    async with flow.factory() as db:
        await db.execute(text('PRAGMA foreign_keys=OFF'))
        await db.commit()
    await flow.client.delete('/api/v1/study/plans/'+plan,headers=flow.alice)
    async with flow.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyGoal))==0
        assert await db.scalar(select(func.count()).select_from(StudyReminder))==0


async def test_owned_reminder_requires_matching_plan_and_goal(flow):
    first=await create(flow)
    second=await create(flow)
    goal=(await detail(flow,second))['goals'][0]['id']
    response=await flow.client.post('/api/v1/study/reminders',headers=flow.alice,json={'plan_id':first,'goal_id':goal,'message':'不一致关联','remind_at':'2026-10-08T23:00:00Z'})
    assert response.status_code==400


@pytest.mark.parametrize('required',[False,True])
async def test_anonymous_access_respects_desktop_login_mode(flow,monkeypatch,required):
    monkeypatch.setattr(study.settings,'desktop_require_login',required)
    response=await flow.client.post('/api/v1/study/plans',json={'name':'本地计划'})
    assert response.status_code==(401 if required else 200)
    async with flow.factory() as db:
        rows=(await db.execute(select(StudyPlan))).scalars().all()
        assert len(rows)==(0 if required else 1)
        if rows:
            assert rows[0].user_id=='local_user'


async def test_missing_resources_and_invalid_reminder_date_do_not_write(flow):
    for method,path,body in [('get','plans/missing',None),('put','goals/missing/toggle',None),('post','reminders/missing/read',None)]:
        response=await flow.client.request(method,'/api/v1/study/'+path,headers=flow.alice,json=body)
        assert response.status_code==404
    response=await flow.client.post('/api/v1/study/reminders',headers=flow.alice,json={'message':'提醒','remind_at':'wrong'})
    assert response.status_code==400
    async with flow.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyReminder))==0


async def test_daily_reminder_becomes_due_again_and_one_time_read_stays_hidden(flow,monkeypatch):
    repeat=await reminder(flow,repeat_daily=True)
    once=await reminder(flow)
    for rid in (repeat,once):
        await flow.client.post('/api/v1/study/reminders/'+rid+'/read',headers=flow.alice)
    class Tomorrow(datetime):
        @classmethod
        def now(cls,tz=None):
            value=cls(2026,10,10,0,0,tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(study,'datetime',Tomorrow)
    data=(await flow.client.get('/api/v1/study/reminders/due',headers=flow.alice)).json()
    assert [r['id'] for r in data['reminders']]==[repeat]


async def test_goal_delete_removes_associated_reminder_only(flow):
    plan=await create(flow)
    goal=(await detail(flow,plan))['goals'][0]['id']
    await reminder(flow,goal_id=goal)
    other=await reminder(flow)
    response=await flow.client.delete('/api/v1/study/goals/'+goal,headers=flow.alice)
    assert response.status_code==200
    data=(await flow.client.get('/api/v1/study/reminders/due',headers=flow.alice)).json()
    assert [r['id'] for r in data['reminders']]==[other]


async def test_notification_single_read_delete_and_empty_batch_preserve_foreign_account(flow):
    assert (await flow.client.put('/api/v1/notifications/na/read',headers=flow.alice)).status_code==200
    data=(await flow.client.get('/api/v1/notifications',headers=flow.alice,params={'is_read':'1'})).json()
    assert data['total']==1 and data['unread_count']==0
    assert (await flow.client.delete('/api/v1/notifications/nb',headers=flow.alice)).status_code==404
    assert (await flow.client.delete('/api/v1/notifications',headers=flow.alice,params={'ids':' , '})).status_code==400
    assert (await flow.client.delete('/api/v1/notifications/na',headers=flow.alice)).status_code==200
    assert (await flow.client.get('/api/v1/notifications',headers=flow.alice)).json()['total']==0
    assert (await flow.client.get('/api/v1/notifications',headers=flow.bob)).json()['unread_count']==1
