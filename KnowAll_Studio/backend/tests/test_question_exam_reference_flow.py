"""Exact persisted paper refs and invalid-paper zero-write/retry. No external AI."""
import asyncio,hashlib,json,os,socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest,uvicorn
from sqlalchemy import delete,event,text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import QuestionBank,ExamPaper,User
from app.api import questions as bank
from app.core.auth import create_access_token
from app.core.api_scheduler import api_client
from tests.test_question_delete_boundaries_flow import flow,foreign_keys,BASE

NORMAL=['q1','q2','q3']
BAD=['partial','all-missing','empty','json-string','json-object','mixed-non-string']
TABLES=['question_bank','knowledge_coverage','exam_papers','answer_records','error_log','exam_submissions','review_queue']
def question(qid):
 return QuestionBank(id=qid,question_type='single_choice',question_text='试卷关联 '+qid,correct_answer='A',options=[{'label':'A','text':'甲'},{'label':'B','text':'乙'}])

@pytest.fixture
def proof(request):
 project=Path(__file__).resolve().parents[2]
 scope=['backend/app/api/questions.py','backend/app/core/derived_document_access.py','backend/app/core/auth.py','backend/app/core/quiz/__init__.py','backend/app/models/__init__.py','backend/tests/test_question_delete_boundaries_flow.py','backend/tests/test_question_exam_reference_flow.py']
 p={'test':request.node.nodeid,'start':datetime.now().astimezone().isoformat(),'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in scope},'http':[],'aborted_http':[],'external_ai_calls':0,'simulation':['AI adapter fail-fast; grade spy delegates actual single-choice grader; cursor gating only when explicitly recorded']}
 yield p
 p['end']=datetime.now().astimezone().isoformat()
 if dest:=os.environ.get('QUESTION_EXAM_PROOF_DIR'):
  d=Path(dest);d.mkdir(parents=True,exist_ok=True);(d/(hashlib.sha256(p['test'].encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(p,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

@pytest.fixture
async def prepared(flow,proof,monkeypatch):
 async with flow.factory() as db:
  db.add_all([User(id=u,username=u,email=u+'@exam.invalid',password_hash='fixture-no-login') for u in ['alice','bob']]);db.add(question('unrelated'));await db.commit()
 flow.alice={'Authorization':'Bearer '+create_access_token('alice')};flow.bob={'Authorization':'Bearer '+create_access_token('bob')}
 flow.grades=[];original=bank.exam_engine.grade_enhanced
 async def graded(*a,**kw):
  flow.grades.append(json.loads(json.dumps({'questions':a[0],'answers':a[1],'options':kw})))
  return await original(*a,**kw)
 monkeypatch.setattr(bank.exam_engine,'grade_enhanced',graded);proof['database']=flow.engine.url.database
 yield flow
 proof['model_calls']=api_client.generate.await_count;proof['grading_calls']=flow.grades
 assert not api_client.generate.await_count

async def snapshot(f):
 async with f.factory() as db:
  return {table:[dict(r) for r in (await db.execute(text('SELECT * FROM '+table+' ORDER BY '+('user_id,paper_id' if table=='exam_submissions' else 'id')))).mappings().all()] for table in TABLES}

async def call(f,client,p,method,path,body=None,actor='alice'):
 request={'method':method,'path':path,'actor':actor,'request':json.loads(json.dumps(body))}
 try:r=await client.request(method,path,json=body,headers=getattr(f,actor) if actor else {})
 except BaseException as e:
  p['aborted_http'].append(request|{'exception':type(e).__name__});raise
 try:payload=r.json()
 except ValueError:payload=r.text
 p['http'].append(request|{'status':r.status_code,'response':payload});return r

async def remove(f,client,p,mode,ids,actor='alice'):
 return await call(f,client,p,'DELETE',BASE+'/'+ids[0],actor=actor) if mode=='single' else await call(f,client,p,'POST',BASE+'/delete-batch',ids,actor)

async def seed_paper(f,ids,paper='paper'):
 async with f.factory() as db:db.add(ExamPaper(id=paper,title='fixture-paper',question_ids=ids));await db.commit()

async def submit(f,client,p,paper='paper'):
 return await call(f,client,p,'POST','/api/v1/quiz/exam/submit',{'paper_id':paper,'answers':{'q1':'B','q2':'A','q3':'A'},'enable_semantic':False})

async def guard_scenario(f,client,p,fk,mode,position):
 await foreign_keys(f,fk);ids=['q2','q3'];ids.insert(position,'q1');await seed_paper(f,ids)
 p.update(fk=fk,kind='delete-guard',mode=mode,position=position,before=await snapshot(f))
 r=await remove(f,client,p,mode,['q1','q2','missing','q1']);p['after_rejected']=await snapshot(f)
 assert r.status_code==409,r.text
 assert p['after_rejected']==p['before'] and not f.grades
 assert '关联' in r.json()['detail'] and 'paper' not in r.text and 'bob' not in r.text
 r=await remove(f,client,p,'single',['unrelated']);assert r.status_code==200
 r=await submit(f,client,p);assert r.status_code==200,r.text
 assert len(r.json()['details'])==3 and r.json()['score']==10
 p['final']=await snapshot(f)
 assert len(p['final']['answer_records'])==3 and len(p['final']['error_log'])==1 and len(p['final']['exam_submissions'])==1
 assert len(f.grades)==1

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
@pytest.mark.parametrize('position',[0,1,2])
async def test_existing_paper_guards_exact_members_and_complete_submit(prepared,proof,fk,mode,position):
 await guard_scenario(prepared,prepared.client,proof,fk,mode,position)

@pytest.mark.parametrize('fk',[True,False])
async def test_exact_ids_ignore_substrings_and_non_array_json(prepared,proof,fk):
 f=prepared;await foreign_keys(f,fk);qid='q"_%[,]中文'
 async with f.factory() as db:db.add(question(qid));await db.commit()
 for i,ids in enumerate([[qid+'tail','prefix'+qid],qid,{'id':qid},[{'id':qid}]]):await seed_paper(f,ids,'near'+str(i))
 r=await remove(f,f.client,proof,'batch',[qid]);assert r.status_code==200,r.text
 async with f.factory() as db:db.add(question(qid));await db.commit()
 await seed_paper(f,['unrelated',qid],'exact');proof['before']=await snapshot(f)
 r=await remove(f,f.client,proof,'batch',[qid]);proof['final']=await snapshot(f)
 assert r.status_code==409,r.text
 assert proof['final']==proof['before']

@pytest.mark.parametrize('actor',['alice',None])
@pytest.mark.parametrize('mode',['single','batch'])
async def test_private_question_404_precedes_exam_conflict(prepared,proof,actor,mode):
 f=prepared
 async with f.factory() as db:q=await db.get(QuestionBank,'q1');q.review_scores={'knowledge_owner_id':'bob'};await db.commit()
 await seed_paper(f,NORMAL);proof['before']=await snapshot(f)
 r=await remove(f,f.client,proof,mode,['q1','q2'],actor);proof['final']=await snapshot(f)
 assert r.status_code==404 and r.json()['detail']=='Question not found' and proof['final']==proof['before']
 r=await submit(f,f.client,proof);assert r.status_code==404 and not f.grades

async def invalid_scenario(f,client,p,fk,kind):
 await foreign_keys(f,fk)
 ids={'empty':[],'json-string':'q1','json-object':{'id':'q1'},'mixed-non-string':['q1',1]}.get(kind,NORMAL)
 await seed_paper(f,ids)
 if kind in ['partial','all-missing']:
  async with f.factory() as db:await db.execute(delete(QuestionBank).where(QuestionBank.id.in_(['q1'] if kind=='partial' else NORMAL)));await db.commit()
 p.update(fk=fk,kind=kind,before=await snapshot(f))
 r=await submit(f,client,p);p['after_rejected']=await snapshot(f)
 assert r.status_code==409,r.text
 assert p['after_rejected']==p['before'] and not f.grades
 async with f.factory() as db:
  paper=await db.get(ExamPaper,'paper');paper.question_ids=NORMAL
  for qid in NORMAL:
   if await db.get(QuestionBank,qid) is None:db.add(question(qid))
  await db.commit()
 r=await submit(f,client,p);assert r.status_code==200,r.text
 assert r.json()['score']==10 and len(r.json()['details'])==3 and len(f.grades)==1
 p['final']=await snapshot(f)
 assert len(p['final']['answer_records'])==3 and len(p['final']['exam_submissions'])==1 and len(p['final']['error_log'])==1

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('kind',BAD)
async def test_invalid_paper_never_grades_or_commits_and_can_retry(prepared,proof,fk,kind):
 await invalid_scenario(prepared,prepared.client,proof,fk,kind)

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
async def test_real_create_then_delete_keeps_saved_paper_usable(prepared,proof,fk,mode):
 f=prepared;await foreign_keys(f,fk)
 created=await call(f,f.client,proof,'POST','/api/v1/quiz/exam/create',{'title':'真实组卷链','question_ids':NORMAL},actor='bob');assert created.status_code==200,created.text
 paper=created.json()['paper_id'];proof['before']=await snapshot(f)
 r=await remove(f,f.client,proof,mode,['q1','q2']);proof['after_rejected']=await snapshot(f)
 assert r.status_code==409 and proof['after_rejected']==proof['before']
 r=await submit(f,f.client,proof,paper);assert r.status_code==200 and len(r.json()['details'])==3
 proof['final']=await snapshot(f)

@pytest.mark.parametrize('fk',[True,False])
async def test_duplicate_paper_ids_keep_existing_unique_grading_contract(prepared,proof,fk):
 f=prepared;await foreign_keys(f,fk);await seed_paper(f,['q1','q1','q2'])
 r=await submit(f,f.client,proof);assert r.status_code==200 and len(r.json()['details'])==2 and r.json()['score']==5
 proof['final']=await snapshot(f);assert len(proof['final']['answer_records'])==2 and len(proof['final']['exam_submissions'])==1

class TrackedStream:
 def __init__(self,source,p,mode='ok',gate=None):self.source=source;self.p=p;self.mode=mode;self.gate=gate
 def __aiter__(self):return self
 async def __anext__(self):
  if self.mode=='error':raise RuntimeError('Explicit cursor read fault')
  if self.mode=='cancel':self.gate.set();await asyncio.Event().wait()
  row=await self.source.__anext__();self.p['stream_rows']=self.p.get('stream_rows',0)+1;return row
 async def close(self):
  await self.source.close();self.p['stream_closed']=self.source.closed;self.p['stream_close_count']=self.p.get('stream_close_count',0)+1

def track_stream(monkeypatch,p,mode='ok',gate=None):
 original=AsyncSession.stream_scalars
 async def stream(db,statement,*a,**kw):
  result=await original(db,statement,*a,**kw)
  p['yield_per']=statement.get_execution_options().get('yield_per');return TrackedStream(result,p,mode,gate)
 monkeypatch.setattr(AsyncSession,'stream_scalars',stream)

@pytest.mark.parametrize('mode',['ok','error','cancel'])
async def test_paper_cursor_is_closed_on_completion_failure_and_cancel(prepared,proof,monkeypatch,mode):
 f=prepared;await seed_paper(f,['q3']);proof['before']=await snapshot(f);proof['cursor_simulation']=mode
 gate=asyncio.Event();track_stream(monkeypatch,proof,mode,gate)
 if mode=='cancel':
  task=asyncio.create_task(remove(f,f.client,proof,'single',['q1']))
  try:
   await asyncio.wait_for(gate.wait(),3);task.cancel()
   with pytest.raises(asyncio.CancelledError):await task
  finally:
   if not task.done():task.cancel()
   await asyncio.gather(task,return_exceptions=True)
 else:
  r=await remove(f,f.client,proof,'single',['q1']);assert r.status_code==(200 if mode=='ok' else 500),r.text
 proof['final']=await snapshot(f)
 assert proof.get('stream_closed') and proof.get('stream_close_count')==1
 if mode!='ok':assert proof['final']==proof['before']

@pytest.mark.parametrize('count',[1,201])
async def test_paper_scan_uses_one_select_and_configured_batches(prepared,proof,monkeypatch,count):
 f=prepared
 async with f.factory() as db:db.add_all([ExamPaper(id='scan'+str(i),title='非关联',question_ids=['different'+str(i)]) for i in range(count)]);await db.commit()
 track_stream(monkeypatch,proof);statements=[]
 def record(connection,cursor,statement,*a):
  if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
 event.listen(f.engine.sync_engine,'before_cursor_execute',record)
 try:r=await remove(f,f.client,proof,'batch',['q1','q2','q1']);proof['select_count']=len(statements);proof['selects']=list(statements)
 finally:event.remove(f.engine.sync_engine,'before_cursor_execute',record)
 assert r.status_code==200 and r.json()['deleted_count']==2
 assert proof.get('stream_rows')==count and proof.get('stream_closed') and proof.get('yield_per')==200 and proof['select_count']<=8
 proof['final']=await snapshot(f)

@pytest.fixture
async def native(prepared,proof):
 listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(prepared.client._transport.app,log_level='error',lifespan='off',ws='none'));task=asyncio.create_task(server.serve(sockets=[listener]));proof['port']=port
 try:
  async with asyncio.timeout(5):
   while not server.started:
    if task.done():await task
    await asyncio.sleep(.01)
  async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10) as client:yield SimpleNamespace(flow=prepared,client=client)
 finally:
  server.should_exit=True
  try:await asyncio.wait_for(task,5)
  except TimeoutError:task.cancel();await asyncio.gather(task,return_exceptions=True)
  listener.close()

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('kind',['delete','partial','all-missing','json-string'])
async def test_native_exam_guard_and_invalid_recovery(native,proof,fk,kind):
 if kind=='delete':await guard_scenario(native.flow,native.client,proof,fk,'batch',1)
 else:await invalid_scenario(native.flow,native.client,proof,fk,kind)
