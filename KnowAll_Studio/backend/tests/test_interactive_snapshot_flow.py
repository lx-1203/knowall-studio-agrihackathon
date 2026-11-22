"""Real local grade and isolated SQL; updates are explicit fixture operations."""
import pytest
from sqlalchemy import event,text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_interactive_session_boundaries_flow import flow,start,submit,records,BASE,OPTIONS
from app.api import interactive_quiz as routes
from app.api import questions as bank
from app.models import QuestionBank

CHOICES=OPTIONS+[{'label':'C','text':'丙'}]
BOOL=[{'label':'A','text':'正确'},{'label':'B','text':'错误'}]
CASES=[('single_choice','A','B','A','B',CHOICES),('multi_choice','A,C','B','C,A','B',CHOICES),
       ('true_false','A','B','正确','错误',BOOL),('fill_blank','0','1','0','1',[]),
       ('short_answer','reliable transfer','unique routing','reliable transfer','unique routing',[]),
       ('calculation','2','20','2.005','20',[])]
async def change(f,**fields):
    async with f.factory() as db:
        q=await db.get(QuestionBank,'q1')
        for name,value in fields.items():setattr(q,name,value)
        await db.commit()

@pytest.mark.parametrize('kind,old,new,old_input,new_input,options',CASES)
@pytest.mark.parametrize('which',['old-correct','new-only'])
async def test_active_session_grades_displayed_version_after_sql_change(flow,monkeypatch,kind,old,new,old_input,new_input,options,which):
    await change(flow,question_type=kind,correct_answer=old,options=options,question_text='开始时显示的原题',analysis='原题解析')
    first=await start(flow);sid=first['session_id'];client=next(q for q in first['questions'] if q['id']=='q1')
    assert client['question_text']=='开始时显示的原题' and 'correct_answer' not in client and 'analysis' not in client
    await change(flow,correct_answer=new,question_text='后台改版后新题',analysis='改版解析')
    seen=[];original=routes.exam_engine.grade
    def observe(**kwargs):seen.append(kwargs['paper']['questions'][0].copy());return original(**kwargs)
    monkeypatch.setattr(routes.exam_engine,'grade',observe)
    value=old_input if which=='old-correct' else new_input
    r=await submit(flow,sid,answer=value,time_spent_ms=31)
    assert r.status_code==200,r.text
    assert r.json()['is_correct'] is (which=='old-correct')
    assert r.json()['correct_answer']==old and r.json()['analysis']=='原题解析'
    assert seen[0]['question_text']=='开始时显示的原题' and seen[0]['options']==options
    row=(await records(flow))[0];assert row.user_answer==value and row.is_correct is (which=='old-correct') and row.time_spent_ms==31
    stats=(await flow.client.get(BASE+f'/session/{sid}/stats')).json()
    assert stats['total_answered']==1 and stats['correct']==int(which=='old-correct')
    assert routes._sessions[sid]['questions'][0]['correct_answer']==old

async def test_option_semantics_are_frozen_even_when_answer_label_unchanged(flow):
    await change(flow,question_type='true_false',correct_answer='A',options=BOOL)
    sid=(await start(flow))['session_id']
    await change(flow,options=[{'label':'A','text':'错误'},{'label':'B','text':'正确'}])
    r=await submit(flow,sid,answer='正确');assert r.status_code==200 and r.json()['is_correct']
    fresh=(await start(flow))['session_id'];r=await submit(flow,fresh,answer='错误');assert r.status_code==200 and r.json()['is_correct']

async def test_question_type_is_frozen_when_answer_text_unchanged(flow):
    await change(flow,question_type='multi_choice',correct_answer='A,C',options=CHOICES)
    sid=(await start(flow))['session_id'];await change(flow,question_type='fill_blank',options=[])
    r=await submit(flow,sid,answer='C,A');assert r.status_code==200 and r.json()['is_correct']
    fresh=(await start(flow))['session_id'];r=await submit(flow,fresh,answer='C,A');assert r.status_code==200 and not r.json()['is_correct']

@pytest.mark.parametrize('mode',['fresh-session','legacy'])
async def test_new_session_and_standalone_use_current_sql_version(flow,mode):
    await start(flow);await change(flow,correct_answer='B',question_text='改版后的当前题',analysis='当前解析')
    sid=(await start(flow))['session_id'] if mode=='fresh-session' else None
    r=await submit(flow,sid,answer='B');assert r.status_code==200 and r.json()['is_correct'] and r.json()['analysis']=='当前解析'
    assert r.json()['correct_answer']=='B' and len(await records(flow))==1

@pytest.mark.parametrize('mode',['single','batch'])
async def test_deleted_question_is_rejected_with_foreign_keys_and_other_question_continues(flow,mode):
    flow.client._transport.app.include_router(bank.router)
    # The bank router shares the fixture dependency override already installed.
    def enable(dbapi_connection,*args):
        cur=dbapi_connection.cursor();cur.execute('PRAGMA foreign_keys=ON');cur.close()
    event.listen(flow.engine.sync_engine,'checkout',enable)
    async with flow.factory() as db:assert (await db.execute(text('PRAGMA foreign_keys'))).scalar()==1
    sid=(await start(flow))['session_id']
    r=await flow.client.delete('/api/v1/quiz/bank/q1') if mode=='single' else await flow.client.post('/api/v1/quiz/bank/delete-batch',json=['q1'])
    assert r.status_code==200,r.text
    r=await submit(flow,sid);assert r.status_code==404 and await records(flow)==[] and routes._sessions[sid]['answers']=={}
    assert (await submit(flow,sid,qid='q2')).status_code==200
    fresh=await start(flow);assert [q['id'] for q in fresh['questions']]==['q2']

async def test_failed_commit_retry_uses_original_snapshot_after_bank_changes(flow,monkeypatch):
    sid=(await start(flow))['session_id'];original=AsyncSession.commit;failed=False
    async def fail_once(db):
        nonlocal failed
        if not failed and any(type(x).__name__=='AnswerRecord' for x in db.new):failed=True;raise RuntimeError('Explicit fixture commit failure')
        return await original(db)
    monkeypatch.setattr(AsyncSession,'commit',fail_once)
    r=await submit(flow,sid);assert r.status_code==500 and await records(flow)==[]
    await change(flow,correct_answer='B',analysis='重试前改版')
    r=await submit(flow,sid);assert r.status_code==200 and r.json()['is_correct'] and r.json()['correct_answer']=='A'
    assert len(await records(flow))==1 and len(routes._sessions[sid]['answers'])==1

async def test_changed_snapshot_remains_private_and_duplicate_still_rejected(flow):
    sid=(await start(flow))['session_id'];await change(flow,correct_answer='B')
    assert (await submit(flow,sid,user='B')).status_code==404 and await records(flow)==[]
    assert (await submit(flow,sid)).status_code==200
    assert (await submit(flow,sid)).status_code==409 and len(await records(flow))==1
