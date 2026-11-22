"""ISO UTC boundary: actual local SQL/JWT/TCP, fixed clock, no external AI."""
import asyncio,hashlib,json,os,socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest,uvicorn
from sqlalchemy import select
from app.api import study
from app.models import StudyPlan,StudyGoal,StudyReminder
from tests.test_study_notification_flow import flow

BAD=['0001-01-01T00:00:00+08:00','0001-01-01T00:00:00+23:59','9999-12-31T23:59:59-08:00','9999-12-31T23:59:59-23:59']
KINDS=['plan','plan-goals','goal','update','reminder']

@pytest.fixture
def proof(request):
 project=Path(__file__).resolve().parents[2]
 scope=['backend/app/api/study.py','backend/app/core/auth.py','backend/app/database.py','backend/app/models/__init__.py','backend/tests/test_study_notification_flow.py','backend/tests/test_study_date_boundary_flow.py']
 p={'test':request.node.nodeid,'start':datetime.now().astimezone().isoformat(),'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in scope},'external_ai_calls':0,'simulation':['study datetime.now fixed 2026-10-09 UTC via shared fixture; AI adapter forbidden']}
 yield p
 p['end']=datetime.now().astimezone().isoformat()
 if dest:=os.environ.get('STUDY_DATE_PROOF_DIR'):
  d=Path(dest);d.mkdir(parents=True,exist_ok=True);(d/(hashlib.sha256(p['test'].encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')

@pytest.mark.parametrize('bad',BAD)
def test_utc_overflow_is_invalid_date(proof,bad):
 assert study._parse_iso(bad) is None
 proof['input']=bad

@pytest.mark.parametrize('value,expected',[
 ('0001-01-01T08:00:00+08:00','0001-01-01T00:00:00'),
 ('9999-12-31T15:59:59-08:00','9999-12-31T23:59:59'),
 ('0001-01-01','0001-01-01T00:00:00'),('9999-12-31T23:59:59','9999-12-31T23:59:59'),
 ('2026-10-10T00:30:00+08:00','2026-10-09T16:30:00'),
 ('2026-10-09T16:30:00Z','2026-10-09T16:30:00'),
 ('2026-10-09','2026-10-09T00:00:00'),(None,None),('',None),('wrong',None)])
def test_representable_and_legacy_date_contract(proof,value,expected):
 actual=study._parse_iso(value);assert (actual.isoformat() if actual else None)==expected
 proof.update(input=value,result=actual.isoformat() if actual else None)

async def rows(factory):
 async with factory() as db:
  return {'plans':[{'id':p.id,'user_id':p.user_id,'name':p.name,'target_end_date':p.target_end_date.isoformat() if p.target_end_date else None} for p in await db.scalars(select(StudyPlan).order_by(StudyPlan.id))],
          'goals':[{'id':g.id,'plan_id':g.plan_id,'title':g.title,'due_date':g.due_date.isoformat() if g.due_date else None} for g in await db.scalars(select(StudyGoal).order_by(StudyGoal.id))],
          'reminders':[{'id':r.id,'user_id':r.user_id,'remind_at':r.remind_at.isoformat(),'message':r.message} for r in await db.scalars(select(StudyReminder).order_by(StudyReminder.id))]}

async def scenario(flow,client,proof,kind,bad):
 proof['http']=[]
 async def call(method,url,body=None,headers=None):
  response=await client.request(method,'/api/v1/study/'+url,headers=headers or flow.alice,json=body)
  try:payload=response.json()
  except ValueError:payload=response.text
  proof['http'].append({'method':method,'url':url,'request':json.loads(json.dumps(body)),'status':response.status_code,'response':payload})
  return response
 seed=await call('POST','plans',{'name':'原计划','target_end_date':'2026-10-20T00:00:00Z','goals':[{'title':'原目标'}]});assert seed.status_code==200,seed.text
 plan=seed.json()['plan_id'];before=await rows(flow.factory);proof['before']=before
 if kind=='plan':method,url,body='POST','plans',{'name':'时间计划','target_end_date':bad}
 elif kind=='plan-goals':method,url,body='POST','plans',{'name':'时间计划','goals':[{'title':'新目标','due_date':bad}]}
 elif kind=='goal':method,url,body='POST','goals',{'plan_id':plan,'title':'新目标','due_date':bad}
 elif kind=='update':method,url,body='PUT','plans/'+plan,{'name':'不应污染','target_end_date':bad}
 else:method,url,body='POST','reminders',{'plan_id':plan,'message':'时间提醒','remind_at':bad}
 rejected=await call(method,url,body);after=await rows(flow.factory)
 proof.update(input=bad,kind=kind,after_rejected=after)
 assert after==before,'Rejected date mutated saved data'
 assert rejected.status_code==(400 if kind=='reminder' else 422),rejected.text
 field='remind_at' if kind=='reminder' else 'due_date' if kind=='goal' else 'target_end_date'
 if kind=='plan-goals':body['goals'][0]['due_date']='2026-10-10T00:30:00+08:00'
 else:body[field]='2026-10-10T00:30:00+08:00'
 recovered=await call(method,url,body);assert recovered.status_code==200,recovered.text
 detail=await call('GET','plans/'+plan);assert detail.status_code==200
 foreign=await call('GET','plans/'+plan,headers=flow.bob);assert foreign.status_code==404
 due=await call('GET','reminders/due');assert due.status_code==200
 proof['after_recovery']=await rows(flow.factory);proof['model_calls']=flow.model.await_count
 assert not flow.model.await_count
 saved=proof['after_recovery'];expected='2026-10-09T16:30:00';proof['expected_date']=expected
 if kind=='reminder':assert saved['reminders'][0]['remind_at']==expected and saved['reminders'][0]['user_id']=='alice'
 elif kind in ('goal','plan-goals'):assert next(g for g in saved['goals'] if g['title']=='新目标')['due_date']==expected
 else:assert next(p for p in saved['plans'] if p['name']=='时间计划' or p['name']=='不应污染')['target_end_date']==expected

@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('bad',BAD)
async def test_asgi_rejection_keeps_data_and_allows_retry(flow,proof,kind,bad):
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=flow.client._transport.app,raise_app_exceptions=False),base_url='http://date') as client:
  await scenario(flow,client,proof,kind,bad)

@pytest.fixture
async def native(flow,proof):
 app=flow.client._transport.app
 listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off',ws='none'));task=asyncio.create_task(server.serve(sockets=[listener]))
 proof.update(port=port,database=flow.factory.kw['bind'].url.database)
 try:
  async with asyncio.timeout(5):
   while not server.started:
    if task.done():await task
    await asyncio.sleep(.01)
  async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10) as client:yield SimpleNamespace(flow=flow,client=client)
 finally:
  server.should_exit=True
  try:await asyncio.wait_for(task,5)
  except TimeoutError:task.cancel();await asyncio.gather(task,return_exceptions=True)
  listener.close()

@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('bad',[BAD[0],BAD[2]])
async def test_native_utc_boundary_failure_and_recovery(native,proof,kind,bad):
 await scenario(native.flow,native.client,proof,kind,bad)
