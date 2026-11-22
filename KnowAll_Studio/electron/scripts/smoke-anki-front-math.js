// Native client -> freshly extracted cloud -> format-2 fixture -> real Anki.
const {startCloud,request,launchClient} = require('./smoke-flashcard-export')
const {spawnSync} = require('node:child_process')
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict')

async function run() {
  const output=process.env.KNOWALL_EXPORT_SMOKE_OUTPUT
  assert.ok(output && process.env.KNOWALL_TEST_PYTHON && process.env.KNOWALL_FORMAT2_SOURCE)
  await fs.mkdir(output,{recursive:true})
  const data=await fs.mkdtemp(path.join(os.tmpdir(),'knowall-front-math-'))
  // Only our child inherits a private temporary root; restore this Node process.
  const prior={TEMP:process.env.TEMP,TMP:process.env.TMP}
  process.env.TEMP=process.env.TMP=data
  const cloud=startCloud()
  for(const key of ['TEMP','TMP']) if(prior[key]===undefined)delete process.env[key];else process.env[key]=prior[key]
  let client,page,report
  const python=(mode,file,deck)=>{
    const p=spawnSync(process.env.KNOWALL_TEST_PYTHON,['-c',String.raw`
import sys,json,sqlite3,zipfile,importlib.util
from pathlib import Path
from anki.collection import Collection,ImportAnkiPackageRequest
data=Path(sys.argv[1]);mode=sys.argv[2];file=Path(sys.argv[3]);out=Path(sys.argv[4]);deck=json.loads(sys.argv[6])
assert data.name.startswith('knowall-front-math-')
dbs=list(data.glob('knowall-cloud-integration-*/workspaces/*/learning.sqlite'))
db=None
for candidate in dbs:
 with sqlite3.connect(candidate) as conn:
  if conn.execute('select count(*) from flashcards where deck_id=?',(deck['id'],)).fetchone()[0]:db=candidate
assert db is not None and db.is_relative_to(data)
with sqlite3.connect(db) as conn:
 conn.row_factory=sqlite3.Row
 cards=[dict(r) for r in conn.execute('select * from flashcards where deck_id=? order by id',(deck['id'],))]
 assert len(cards)==3
 # The sole deliberate fixture mutation represents a package made by format 2.
 if mode=='old':
  conn.execute('update flashcards set anki_export_format=2 where deck_id=?',(deck['id'],));conn.commit()
  source=Path(sys.argv[5]);old=importlib.util.module_from_spec(importlib.util.spec_from_loader('format2',loader=None))
  exec(compile(source.read_text(encoding='utf-8'),str(source),'exec'),old.__dict__)
  assert old.EXPORT_FORMAT_VERSION==2
  for c in cards:c['tags']=json.loads(c['tags']) if isinstance(c['tags'],str) else c['tags']
  old.export_apkg(cards,deck['name'],str(file),deck_id=deck['id'])
 else:
  assert all(c['anki_export_format']==3 for c in cards)
col=Collection(str(data/'consumer.anki2'))
try:
 imported=col.import_anki_package(ImportAnkiPackageRequest(package_path=str(file)))
 assert col.note_count()==3 and col.card_count()==4
 notes=[col.get_note(nid) for nid in col.find_notes('')]
 for n in notes:
  if mode=='old':assert '$' in n.fields[0]
  else:assert r'\(' in n.fields[0] or r'\[' in n.fields[0]
 ids=sorted(col.find_cards(''))
 for cid in ids:
  c=col.get_card(cid)
  if mode=='old':c.queue=c.type=2;c.ivl=17;c.reps=4;c.due=35;col.update_card(c)
  assert (c.ivl,c.reps,c.due,c.queue)==(17,4,35,2)
 questions=[];answers=[]
 for cid in ids:
  c=col.get_card(cid);q,a=c.question(),c.answer();questions.append(q);answers.append(a)
  if mode!='old' and c.note().note_type()['type']==1:
   hidden='y_2' if c.ord==0 else 'z_3'
   from lxml.html import fromstring
   assert hidden not in fromstring(q).text_content() and hidden in fromstring(a).text_content()
 if mode!='old':
  (out/'questions.html').write_text('<hr>'.join(questions),encoding='utf-8')
  (out/'answers.html').write_text('<hr>'.join(answers),encoding='utf-8')
 with zipfile.ZipFile(file) as z:
  p=data/'package-check.sqlite';p.write_bytes(z.read('collection.anki2'))
 with sqlite3.connect(p) as conn:mtimes=dict(conn.execute('select guid,mod from notes'))
 print(json.dumps({'cards':ids,'guids':sorted(n.guid for n in notes),'mtimes':mtimes,'updated':len(imported.log.updated),'formats':[2 if mode=='old' else c['anki_export_format'] for c in cards],'versions':{c['id']:c['content_version'] for c in cards},'schedule':[17,4,35,2]}))
finally:col.close()
`,data,mode,file,output,process.env.KNOWALL_FORMAT2_SOURCE,JSON.stringify(deck)],
      {cwd:process.env.KNOWALL_TEST_BACKEND,windowsHide:true,encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}})
    assert.equal(p.status,0,p.stdout+p.stderr)
    return JSON.parse(p.stdout.trim().split('\n').at(-1))
  }
  try {
    const ready=await cloud.event('READY')
    ;({client,page}=await launchClient(process.env.KNOWALL_DESKTOP_EXECUTABLE,path.join(data,'desktop'),ready.origin,ready.accounts[1]))
    const d=await request(page,'/api/v1/flashcards/decks',{name:'题面挖空数学实际升级'});assert.equal(d.status,201);const deck=d.data
    const sources=[['qa','求 $x_1$ 的值'],['compare',String.raw`比较 $$a_1^2+b_2^2$$ 与 $\href{javascript:alert(1)}{bad}$`],['cloze','$x_1+{{c1::y_2::加数}}={{c2::z_3}}$']]
    const cards=[]
    for(const [kind,front] of sources){
      const response=await request(page,'/api/v1/flashcards/cards',{deck_id:deck.id,card_type:kind,front,back:'完整解释 $w_4$',hints:'',tags:[]})
      assert.equal(response.status,201);cards.push(response.data)
    }
    const download=async name=>{
      const response=await page.request.get(new URL('/api/v1/flashcards/export/anki/'+deck.id,page.url()).href,{headers:{Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('knowall_token'))}})
      if(response.status()!==200)console.error('DOWNLOAD_HTTP_FAILURE',name,response.status(),await response.text())
      assert.equal(response.status(),200);const p=path.join(output,name+'.apkg');await fs.writeFile(p,await response.body());return p
    }
    await download('baseline3')
    const before=(await request(page,'/api/v1/flashcards/due?deck_id='+deck.id)).data.cards.map(c=>({id:c.id,schedule:c.schedule}))
    const first=python('old',path.join(output,'format2.apkg'),deck)
    const upgraded=python('upgraded',await download('upgraded'),deck)
    assert.equal(upgraded.updated,3);assert.deepEqual(upgraded.guids,first.guids);assert.deepEqual(upgraded.cards,first.cards)
    for(const guid of first.guids)assert.ok(upgraded.mtimes[guid]>first.mtimes[guid])
    assert.deepEqual(upgraded.versions,first.versions)
    const unchanged=python('unchanged',await download('unchanged'),deck)
    assert.equal(unchanged.updated,0);assert.deepEqual(unchanged.mtimes,upgraded.mtimes)
    await page.getByText('统一复习',{exact:true}).click()
    await page.getByText(cards[0].front,{exact:true}).waitFor()
    await page.getByRole('button',{name:'编辑当前卡片',exact:true}).click()
    await page.getByLabel('问题 / 正面',{exact:true}).fill('求 $q_5$ 的值')
    const saved=page.waitForResponse(r=>r.url().endsWith('/flashcards/cards/'+cards[0].id)&&r.request().method()==='PATCH')
    await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click()
    assert.equal((await saved).status(),200);await page.getByRole('dialog').waitFor({state:'detached'})
    await page.getByText('求 $q_5$ 的值',{exact:true}).waitFor()
    await page.screenshot({path:path.join(output,'current.png'),animations:'disabled'})
    const edited=python('edited',await download('edited'),deck)
    assert.equal(edited.updated,1);assert.deepEqual(edited.guids,first.guids);assert.deepEqual(edited.cards,first.cards)
    assert.equal(edited.versions[cards[0].id],upgraded.versions[cards[0].id]+1)
    assert.deepEqual((await request(page,'/api/v1/flashcards/due?deck_id='+deck.id)).data.cards.map(c=>({id:c.id,schedule:c.schedule})),before)
    cloud.command('recycle');await cloud.event('RECYCLED')
    const restarted=python('restarted',await download('restarted'),deck)
    assert.equal(restarted.updated,0);assert.deepEqual(restarted.mtimes,edited.mtimes);assert.deepEqual(restarted.cards,first.cards)
    const denied=await page.evaluate(async({account,deck})=>{
      const r=await fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(account)});const a=await r.json()
      return (await fetch('/api/v1/flashcards/export/anki/'+deck,{headers:{Authorization:'Bearer '+a.access_token}})).status
    },{account:ready.accounts[0],deck:deck.id})
    assert.equal(denied,404)
    report={at:new Date().toISOString(),passed:true,actual_native_front_edit:true,actual_format2_default_upgrade:true,actual_native_cloze_hiding:true,actual_schedule_identity_preserved:true,actual_restart_stable_mtime:true,actual_foreign_account_404:true,no_http_mocks:true,no_fake_clock:true,no_paid_call:true,fixture_mutation:'Own private temporary DB format marker only set to 2; old source generates real format2 APKG using unchanged persisted mtime; no schedule or content edit',first,upgraded,unchanged,edited,restarted,actual_executable:process.env.KNOWALL_DESKTOP_EXECUTABLE,actual_backend:process.env.KNOWALL_TEST_BACKEND}
  } catch(e){
    console.error('PRIMARY_NATIVE_FAILURE',e.stack || String(e))
    if(cloud.command('diagnostics')) {
      try { console.error('CLOUD_DIAGNOSTICS_AT_PRIMARY',JSON.stringify(await cloud.event('DIAGNOSTICS',10000))) }
      catch(diagnosticError) { console.error('CLOUD_DIAGNOSTICS_FAILED',diagnosticError.message) }
    }
    console.error('CLOUD_STDERR_AT_PRIMARY',cloud.stderr())
    await fs.writeFile(path.join(output,'primary-error.txt'),e.stack || String(e)).catch(()=>{})
    if(page)await page.screenshot({path:path.join(output,'failed.png')}).catch(()=>{})
    throw e
  }
  finally{
    try{if(client)await client.close()}finally{
      try{await cloud.close()}finally{
        assert.equal(path.dirname(path.resolve(data)),path.resolve(os.tmpdir()))
        assert.ok(path.basename(data).startsWith('knowall-front-math-'))
        await fs.rm(data,{recursive:true,force:true})
      }
    }
  }
  report.temporary_data_removed=true
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2))
  console.log('PASS native edit, actual format2 default update, cloze hiding, FSRS/local schedule, restart/private404/cleanup')
}
run().catch(e=>{console.error(e);process.exitCode=1})
