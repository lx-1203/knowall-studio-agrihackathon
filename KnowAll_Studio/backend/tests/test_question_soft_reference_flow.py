"""Soft references: actual delete routers/SQLite/JWT/TCP, AI forbidden."""
import asyncio,hashlib,json,os,socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest,uvicorn
from sqlalchemy import event,text
from app.models import QuestionBank,ReviewQueue,User
from app.core.auth import create_access_token
from app.core.api_scheduler import api_client
from tests.test_question_delete_boundaries_flow import flow,foreign_keys,BASE

@pytest.fixture
def proof(request):
 project=Path(__file__).resolve().parents[2]
 scope=['backend/app/api/questions.py','backend/app/core/derived_document_access.py','backend/app/core/auth.py','backend/app/models/__init__.py','backend/tests/test_question_delete_boundaries_flow.py','backend/tests/test_question_soft_reference_flow.py']
 p={'test':request.node.nodeid,'start':datetime.now().astimezone().isoformat(),'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in scope},'http':[],'simulation':['Only AI adapter fail-fast mock; local DB, JWT, delete/list HTTP are real'],'external_ai_calls':0}
 yield p
 p['end']=datetime.now().astimezone().isoformat()
 if dest:=os.environ.get('QUESTION_SOFT_PROOF_DIR'):
  d=Path(dest);d.mkdir(parents=True,exist_ok=True)
  (d/(hashlib.sha256(p['test'].encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(p,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

@pytest.fixture
async def prepared(flow,proof):
 async with flow.factory() as db:
  db.add_all([User(id=u,username=u,email=u+'@example.invalid',password_hash='fixture-no-login') for u in ['alice','bob']]);await db.commit()
 flow.alice={'Authorization':'Bearer '+create_access_token('alice')}
 flow.bob={'Authorization':'Bearer '+create_access_token('bob')}
 proof['database']=flow.engine.url.database
 yield flow
 proof['model_calls']=api_client.generate.await_count
 assert not api_client.generate.await_count

async def snapshot(f):
 async with f.factory() as db:
  return {table:[dict(r) for r in (await db.execute(text('SELECT * FROM '+table+' ORDER BY id'))).mappings().all()] for table in ['question_bank','knowledge_coverage','review_queue']}

async def call(f,client,p,method,path,body=None,actor='alice'):
 r=await client.request(method,path,json=body,headers=getattr(f,actor) if actor else {})
 p['http'].append({'method':method,'path':path,'actor':actor,'request':json.loads(json.dumps(body)),'status':r.status_code,'response':r.json()})
 return r

async def remove(f,client,p,mode,ids,actor='alice'):
 return await call(f,client,p,'DELETE',BASE+'/'+ids[0],actor=actor) if mode=='single' else await call(f,client,p,'POST',BASE+'/delete-batch',ids,actor)

async def seed_reference(f,kind,owner='bob',completed=False):
 async with f.factory() as db:
  if kind=='variant':
   db.add(QuestionBank(id='variant',parent_question_id='q1',question_type='single_choice',question_text='存活变式',correct_answer='A',review_scores={'knowledge_owner_id':owner}))
  else:
   db.add(ReviewQueue(id='queue',user_id=owner,resource_type=kind,resource_id='q1',completed=completed,reason='manual'))
  await db.commit()

async def conflict_scenario(f,client,p,mode,fk,kind,completed=False,owner='bob'):
 await foreign_keys(f,fk);p.update(fk=fk,kind=kind,mode=mode,completed=completed,reference_owner=owner)
 await seed_reference(f,kind,owner,completed)
 p['before']=await snapshot(f)
 if kind=='variant':
  listed=await call(f,client,p,'GET','/api/v1/quiz/questions',actor='bob');assert listed.status_code==200
  p['visible_before']=[q['id'] for q in listed.json()]
  assert 'variant' in p['visible_before']
 r=await remove(f,client,p,mode,['q1','q2','missing','q1'])
 p['after_rejected']=await snapshot(f)
 assert r.status_code==409,r.text
 assert p['after_rejected']==p['before']
 assert '关联' in r.json()['detail'] and 'bob' not in r.text and 'variant' not in r.text and 'queue' not in r.text
 if kind=='variant':
  listed=await call(f,client,p,'GET','/api/v1/quiz/questions',actor='bob');assert listed.status_code==200
  p['visible_after']=[q['id'] for q in listed.json()]
  assert p['visible_after']==p['visible_before']
 # A rejected batch leaves the connection usable; unrelated q3 still deletes.
 recovery=await remove(f,client,p,'single',['q3']);assert recovery.status_code==200,recovery.text
 p['final']=await snapshot(f)
 assert {q['id'] for q in p['final']['question_bank']}==({'q1','q2','variant'} if kind=='variant' else {'q1','q2'})
 assert {c['id'] for c in p['final']['knowledge_coverage']}=={'cov1','cov2','card-cov'}
 assert p['final']['review_queue']==p['before']['review_queue']

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
@pytest.mark.parametrize('kind',['variant','question'])
async def test_surviving_soft_reference_blocks_without_mutation(prepared,proof,fk,mode,kind):
 await conflict_scenario(prepared,prepared.client,proof,mode,fk,kind)

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
@pytest.mark.parametrize('owner',['alice','bob'])
async def test_completed_question_review_is_also_preserved(prepared,proof,fk,mode,owner):
 await conflict_scenario(prepared,prepared.client,proof,mode,fk,'question',True,owner)

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('grandchild',[True,False])
async def test_batch_all_variants_can_delete_but_surviving_grandchild_blocks(prepared,proof,fk,grandchild):
 f=prepared;await foreign_keys(f,fk)
 await seed_reference(f,'variant','alice')
 if grandchild:
  async with f.factory() as db:
   db.add(QuestionBank(id='grandchild',parent_question_id='variant',question_type='single_choice',question_text='孙题',correct_answer='A'));await db.commit()
 proof['before']=await snapshot(f)
 r=await remove(f,f.client,proof,'batch',['q1','variant','q1','missing'])
 proof['after_first']=await snapshot(f)
 assert r.status_code==(409 if grandchild else 200),r.text
 if grandchild:
  assert proof['after_first']==proof['before']
  r=await remove(f,f.client,proof,'batch',['q1','variant','grandchild','missing']);assert r.status_code==200
 assert r.json()['deleted_count']==(3 if grandchild else 2)
 proof['final']=await snapshot(f)
 assert {q['id'] for q in proof['final']['question_bank']}=={'q2','q3'}
 assert {c['id'] for c in proof['final']['knowledge_coverage']}=={'card-cov','cov2','cov3'}

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
async def test_flashcard_queue_same_id_is_not_question_reference(prepared,proof,fk,mode):
 f=prepared;await foreign_keys(f,fk);await seed_reference(f,'flashcard')
 proof['before']=await snapshot(f);r=await remove(f,f.client,proof,mode,['q1','q1','missing']);assert r.status_code==200,r.text
 if mode=='batch':assert r.json()['deleted_count']==1
 proof['final']=await snapshot(f)
 assert {q['id'] for q in proof['final']['question_bank']}=={'q2','q3'}
 assert proof['final']['review_queue']==proof['before']['review_queue']
 assert {c['id'] for c in proof['final']['knowledge_coverage']}=={'card-cov','cov2','cov3'}

@pytest.mark.parametrize('actor',['alice',None])
@pytest.mark.parametrize('mode',['single','batch'])
async def test_private_parent_hides_references_before_conflict_check(prepared,proof,actor,mode):
 f=prepared
 async with f.factory() as db:
  q=await db.get(QuestionBank,'q1');q.review_scores={'knowledge_owner_id':'bob'};await db.commit()
 await seed_reference(f,'question','bob');proof['before']=await snapshot(f)
 r=await remove(f,f.client,proof,mode,['q1','q2'],actor);proof['final']=await snapshot(f)
 assert r.status_code==404,r.text
 assert proof['final']==proof['before']
 assert r.json()['detail']=='Question not found'

@pytest.mark.parametrize('count',[4,80])
async def test_soft_reference_preflight_has_bounded_selects(prepared,proof,count):
 f=prepared
 ids=['bulk'+str(i) for i in range(count)]
 async with f.factory() as db:
  db.add_all([QuestionBank(id=q,question_type='single_choice',question_text='批量边界',correct_answer='A') for q in ids]);await db.commit()
 statements=[]
 def record(connection,cursor,statement,*args):
  if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
 event.listen(f.engine.sync_engine,'before_cursor_execute',record)
 try:
  r=await remove(f,f.client,proof,'batch',ids+ids+['missing']);proof['select_count']=len(statements);proof['selects']=list(statements)
 finally:event.remove(f.engine.sync_engine,'before_cursor_execute',record)
 assert r.status_code==200,r.text
 assert r.json()['deleted_count']==count and proof['select_count']<=8
 proof['final']=await snapshot(f)
 assert {q['id'] for q in proof['final']['question_bank']}=={'q1','q2','q3'}

@pytest.fixture
async def native(prepared,proof):
 listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(prepared.client._transport.app,log_level='error',lifespan='off',ws='none'))
 task=asyncio.create_task(server.serve(sockets=[listener]));proof['port']=port
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
@pytest.mark.parametrize('mode',['single','batch'])
@pytest.mark.parametrize('kind',['variant','question'])
async def test_native_soft_reference_guard_and_recovery(native,proof,fk,mode,kind):
 await conflict_scenario(native.flow,native.client,proof,mode,fk,kind)
