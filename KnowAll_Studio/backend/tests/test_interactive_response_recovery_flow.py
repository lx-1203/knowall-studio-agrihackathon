"""Read-back of committed interactive grading: actual routes/SQLite, AI forbidden."""
import pytest
from tests.test_interactive_session_boundaries_flow import flow,start,submit,records,BASE

@pytest.mark.parametrize('kind,correct,wrong',[
    ('single_choice','A','B'),('multi_choice','A,B','B'),('true_false','A','B'),
    ('fill_blank','甲','乙'),('short_answer','甲','乙'),('calculation','42','0'),
])
@pytest.mark.parametrize('answer_correct',[True,False])
async def test_read_back_preserves_actual_grade_without_second_write(flow,kind,correct,wrong,answer_correct):
    from app.models import QuestionBank
    async with flow.factory() as db:
        q=await db.get(QuestionBank,'q1');q.question_type=kind;q.correct_answer=correct;q.analysis='原始评分解析'
        if kind=='true_false':q.options=[{'label':'A','text':'正确'},{'label':'B','text':'错误'}]
        await db.commit()
    sid=(await start(flow))['session_id']
    posted=await submit(flow,sid,answer=correct if answer_correct else wrong,time_spent_ms=37)
    assert posted.status_code==200;graded=posted.json()
    response=await flow.client.get(BASE+f'/session/{sid}/stats');assert response.status_code==200
    stats=response.json();assert stats['total_answered']==1
    saved=stats['questions_detail'][0]
    assert saved['analysis']==graded['analysis']
    assert saved['is_correct']==graded['is_correct'];assert saved['correct_answer']==graded['correct_answer']
    assert saved['user_answer']==(correct if answer_correct else wrong);assert saved['time_spent_ms']==37
    assert [a['question_id'] for a in stats['questions_detail']]==['q1']
    assert (await submit(flow,sid,answer=wrong,time_spent_ms=999)).status_code==409
    assert len(await records(flow))==1

async def test_empty_and_private_read_back_never_discloses_unsubmitted_answers(flow):
    sid=(await start(flow))['session_id']
    for uid in ['B','local']:
        assert (await flow.client.get(BASE+f'/session/{sid}/stats',headers={'x-test-user':uid})).status_code==404
    assert (await flow.client.get(BASE+'/session/missing/stats')).status_code==404
    empty=(await flow.client.get(BASE+f'/session/{sid}/stats')).json()
    assert empty['questions_detail']==[];assert empty['total_answered']==0;assert len(await records(flow))==0

async def test_read_back_retains_feedback_after_bank_changes(flow):
    from app.models import QuestionBank
    sid=(await start(flow))['session_id'];r=(await submit(flow,sid)).json()
    async with flow.factory() as db:
        q=await db.get(QuestionBank,'q1');q.correct_answer='B';q.analysis='晚到新解析';await db.commit()
    stats=(await flow.client.get(BASE+f'/session/{sid}/stats')).json()
    assert stats['questions_detail'][0]['analysis']==r['analysis']
    assert stats['questions_detail'][0]['correct_answer']=='A';assert len(await records(flow))==1
