"""Gated actual local grader + real independent SQL writes; no external AI."""
import asyncio,hashlib,json,os,socket
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
import httpx,pytest,uvicorn
from sqlalchemy import event,text,update,delete
from app.models import QuestionBank,ExamPaper,Document,DocumentChunk
from app.api import questions as bank
from app.core.auth import create_access_token
from tests.test_exam_submission_atomic_flow import native as isolated

FIELDS={'question_text':'改版题面','correct_answer':'B','options':[{'label':'A','text':'改选项'},{'label':'B','text':'乙'}],'blanks':[{'index':0,'answer':'changed'}],'scoring_points':[{'point':'changed','weight':1}],'analysis':'改解析','question_type':'multi_choice','cognitive_level':'L6_create','difficulty_score':.9}
CASES=[*FIELDS,'paper_ids','paper_removed','question_removed','document_owner','question_owner']
TABLES=['question_bank','exam_papers','documents','document_chunks','answer_records','error_log','exam_submissions']

@pytest.fixture
def proof(request):
 project=Path(__file__).resolve().parents[2];scope=['backend/app/api/questions.py','backend/app/core/derived_document_access.py','backend/app/core/auth.py','backend/app/core/quiz/__init__.py','backend/app/models/__init__.py','backend/tests/test_exam_submission_atomic_flow.py','backend/tests/test_exam_postgrade_source_flow.py']
 p={'test':request.node.nodeid,'start':datetime.now().astimezone().isoformat(),'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in scope},'http':[],'grades':[],'simulation':['Explicit asyncio grading gate, actual local deterministic grader; independent SQLite write timing controlled; AI fail-fast mock'],'external_ai_calls':0}
 yield p
 p['end']=datetime.now().astimezone().isoformat()
 if dest:=os.environ.get('EXAM_POSTGRADE_PROOF_DIR'):
  out=Path(dest);out.mkdir(parents=True,exist_ok=True);(out/(hashlib.sha256(p['test'].encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(p,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

@pytest.fixture
async def prepared(isolated,proof):
 client,factory,calls=isolated
 async with factory() as db:
  db.add(Document(id='source',filename='temporary.md',file_type='md',sha256='postgrade',metadata_={'knowledge_owner_id':'A'}));await db.flush()
  db.add(DocumentChunk(id='chunk',doc_id='source',chunk_index=0,text_content='原题资料'));await db.flush()
  q=await db.get(QuestionBank,'q1');q.source_chunk_id='chunk';await db.commit()
  engine=db.bind
 proof['database']=engine.url.database
 yield SimpleNamespace(client=client,factory=factory,engine=engine,calls=calls)
 proof['model_calls']=len(calls);assert not calls

async def fk_mode(f,fk):
 def checkout(conn,*a):conn.execute('PRAGMA foreign_keys='+('ON' if fk else 'OFF'))
 event.listen(f.engine.sync_engine,'checkout',checkout)
 async with f.factory() as db:assert (await db.execute(text('PRAGMA foreign_keys'))).scalar()==int(fk)

async def snapshot(f):
 async with f.factory() as db:return {t:[dict(r) for r in (await db.execute(text('SELECT * FROM '+t+' ORDER BY '+('user_id,paper_id' if t=='exam_submissions' else 'id')))).mappings()] for t in TABLES}

async def submit(f,client,p):
 body={'paper_id':'p','answers':{'q0':'B','q1':'A'},'enable_semantic':False}
 r=await client.post('/api/v1/quiz/exam/submit',headers={'Authorization':'Bearer '+create_access_token('A')},json=body)
 try:response=r.json()
 except ValueError:response=r.text
 p['http'].append({'request':json.loads(json.dumps(body)),'status':r.status_code,'response':response});return r

async def change(f,kind):
 async with f.factory() as db:
  if kind in FIELDS:await db.execute(update(QuestionBank).where(QuestionBank.id=='q1').values(**{kind:FIELDS[kind]}))
  elif kind=='paper_ids':await db.execute(update(ExamPaper).where(ExamPaper.id=='p').values(question_ids=['q0']))
  elif kind=='paper_removed':await db.execute(delete(ExamPaper).where(ExamPaper.id=='p'))
  elif kind=='question_removed':await db.execute(delete(QuestionBank).where(QuestionBank.id=='q1'))
  elif kind=='document_owner':await db.execute(update(Document).where(Document.id=='source').values(metadata_={'knowledge_owner_id':'B'}))
  elif kind=='question_owner':await db.execute(update(QuestionBank).where(QuestionBank.id=='q1').values(review_scores={'knowledge_owner_id':'B'}))
  elif kind=='metadata':await db.execute(update(QuestionBank).where(QuestionBank.id=='q1').values(tags=['changed'],review_total=.25));await db.execute(update(ExamPaper).where(ExamPaper.id=='p').values(title='新标题'))
  elif kind!='none':raise AssertionError(kind)
  await db.commit()

async def restore(f,original):
 # Explicit fixture repair using real SQL/ORM, never clears submission/history.
 async with f.factory() as db:
  q=await db.get(QuestionBank,'q1')
  row=next(r for r in original['question_bank'] if r['id']=='q1')
  if q is None:q=QuestionBank(id='q1');db.add(q)
  for key in ['question_type','question_text','correct_answer','options','blanks','scoring_points','analysis','cognitive_level','difficulty_score','review_scores','source_chunk_id']:
   value=row[key];setattr(q,key,json.loads(value) if key in ['options','blanks','scoring_points','review_scores'] and value is not None else value)
  paper=await db.get(ExamPaper,'p')
  if paper is None:paper=ExamPaper(id='p',title='p');db.add(paper)
  paper.question_ids=['q0','q1'];doc=await db.get(Document,'source');doc.metadata_={'knowledge_owner_id':'A'};await db.commit()

async def scenario(f,client,p,monkeypatch,fk,kind):
 await fk_mode(f,fk);p.update(fk=fk,kind=kind,before=await snapshot(f));entered=asyncio.Event();release=asyncio.Event();original=bank.exam_engine.grade_enhanced
 async def grade(*a,**kw):
  p['grades'].append(json.loads(json.dumps({'paper':a[0],'answers':a[1],'options':kw})));entered.set();await asyncio.wait_for(release.wait(),10)
  return await original(*a,**kw)
 monkeypatch.setattr(bank.exam_engine,'grade_enhanced',grade)
 task=asyncio.create_task(submit(f,client,p))
 try:
  await asyncio.wait_for(entered.wait(),10);await change(f,kind);p['after_change']=await snapshot(f);release.set();r=await asyncio.wait_for(task,10)
 finally:
  release.set()
  if not task.done():task.cancel()
  await asyncio.gather(task,return_exceptions=True)
 p['after_response']=await snapshot(f)
 if kind in ['none','metadata']:
  assert r.status_code==200,r.text
  assert r.json()['score']==5 and len(r.json()['details'])==2
  assert len(p['after_response']['answer_records'])==2 and len(p['after_response']['exam_submissions'])==1
 else:
  assert r.status_code==(404 if kind in ['document_owner','question_owner'] else 409),r.text
  assert p['after_response']==p['after_change'] and len(p['grades'])==1
  assert 'score' not in r.json() and '改版' not in r.text and 'B' not in r.text
  await restore(f,p['before']);r=await submit(f,client,p);assert r.status_code==200,r.text
  assert r.json()['score']==5 and len(r.json()['details'])==2 and len(p['grades'])==2
 p['final']=await snapshot(f)
 assert len(p['final']['answer_records'])==2 and len(p['final']['exam_submissions'])==1
 assert len(p['final']['error_log'])==1 and p['final']['error_log'][0]['error_count']==4

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('kind',CASES)
async def test_late_sql_change_is_rejected_and_repair_can_retry(prepared,proof,monkeypatch,fk,kind):
 await scenario(prepared,prepared.client,proof,monkeypatch,fk,kind)

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('kind',['none','metadata'])
async def test_unchanged_grade_and_unrelated_metadata_still_commit(prepared,proof,monkeypatch,fk,kind):
 await scenario(prepared,prepared.client,proof,monkeypatch,fk,kind)

@pytest.fixture
async def tcp(prepared,proof):
 listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1];proof['port']=port
 server=uvicorn.Server(uvicorn.Config(prepared.client._transport.app,log_level='error',lifespan='off',ws='none'));task=asyncio.create_task(server.serve(sockets=[listener]))
 try:
  async with asyncio.timeout(10):
   while not server.started:
    if task.done():await task
    await asyncio.sleep(.01)
  async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10) as client:yield prepared,client
 finally:
  server.should_exit=True
  try:await asyncio.wait_for(task,5)
  except TimeoutError:task.cancel();await asyncio.gather(task,return_exceptions=True)
  listener.close()

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('kind',['correct_answer','paper_removed','question_removed','document_owner'])
async def test_native_late_change_and_manual_recovery(tcp,proof,monkeypatch,fk,kind):
 f,client=tcp;await scenario(f,client,proof,monkeypatch,fk,kind)
