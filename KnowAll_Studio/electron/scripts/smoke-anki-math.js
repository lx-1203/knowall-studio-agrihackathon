// Actual native client -> extracted current cloud -> actual Anki consumer.
const {startCloud, request, launchClient} = require('./smoke-flashcard-export')
const {spawnSync} = require('node:child_process')
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path')
const assert = require('node:assert/strict')

async function smoke() {
  const output = process.env.KNOWALL_EXPORT_SMOKE_OUTPUT
  assert.ok(output && process.env.KNOWALL_TEST_PYTHON)
  await fs.mkdir(output,{recursive:true})
  const data = await fs.mkdtemp(path.join(os.tmpdir(),'knowall-anki-math-'))
  const cloud = startCloud(); let client,page,report
  const consumer = (mode,filename) => {
    const run = spawnSync(process.env.KNOWALL_TEST_PYTHON,['-c',String.raw`
import sys,json,zipfile,sqlite3
from pathlib import Path
from anki.collection import Collection,ImportAnkiPackageRequest
c=Collection(sys.argv[1]);mode=sys.argv[2];p=sys.argv[3]
try:
 with zipfile.ZipFile(p) as z:
  db=Path(sys.argv[1]+'.check');db.write_bytes(z.read('collection.anki2'))
  with sqlite3.connect(db) as conn:mtime=conn.execute('select mod from notes').fetchone()[0]
 c.import_anki_package(ImportAnkiPackageRequest(package_path=p))
 assert c.note_count()==c.card_count()==1
 n=c.get_note(c.find_notes('')[0]);card=c.get_card(c.find_cards('')[0])
 if mode=='first':
  assert r'\(x_1\)' in n.fields[1]
  card.queue=card.type=2;card.ivl=17;card.reps=4;card.due=35;c.update_card(card)
 else:
  assert r'\(a_1^2+b_2^2=c_3^2\)' in n.fields[1] and r'\[\frac{1}{2}\]' in n.fields[1]
  from lxml.html import fromstring
  codes=fromstring(n.fields[1]).xpath('//code');assert any(r'\href' in e.text_content() for e in codes)
  Path(sys.argv[4]).write_text(card.answer(),encoding='utf-8')
 assert (card.ivl,card.reps,card.due,card.queue)==(17,4,35,2)
 print(json.dumps({'guid':n.guid,'note':n.id,'card':card.id,'mtime':mtime,'schedule':[card.ivl,card.reps,card.due,card.queue]}))
finally:c.close()
`,path.join(data,'consumer.anki2'),mode,filename,path.join(output,'answer.html')],
    {windowsHide:true,encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}})
    assert.equal(run.status,0,run.stdout+run.stderr)
    return JSON.parse(run.stdout.trim().split('\n').at(-1))
  }
  try {
    const ready = await cloud.event('READY')
    ;({client,page}=await launchClient(process.env.KNOWALL_DESKTOP_EXECUTABLE,path.join(data,'desktop'),ready.origin,ready.accounts[1]))
    const deck=(await request(page,'/api/v1/flashcards/decks',{name:'原生数学导出牌组'})).data
    const card=(await request(page,'/api/v1/flashcards/cards',{deck_id:deck.id,card_type:'qa',front:'勾股定理的公式是什么？',back:'旧公式 $x_1$',hints:'',tags:[]})).data
    const download=async name=>{
      const response=await page.request.get(new URL('/api/v1/flashcards/export/anki/'+deck.id,page.url()).href,
        {headers:{Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('knowall_token'))}})
      assert.equal(response.status(),200)
      const p=path.join(output,name+'.apkg');await fs.writeFile(p,await response.body());return p
    }
    const first=consumer('first',await download('first'))
    await page.getByText('统一复习',{exact:true}).click()
    await page.getByText(card.front,{exact:true}).waitFor()
    const before=(await request(page,'/api/v1/flashcards/due?deck_id='+deck.id)).data.cards.find(c=>c.id===card.id).schedule
    await page.getByRole('button',{name:'编辑当前卡片',exact:true}).click()
    await page.getByLabel('答案 / 解释',{exact:true}).fill(String.raw`**勾股定理** $a_1^2+b_2^2=c_3^2$。

$$\frac{1}{2}$$

危险扩展保留源码：$\href{javascript:alert(1)}{bad}$`)
    const saved=page.waitForResponse(r=>r.url().endsWith('/flashcards/cards/'+card.id)&&r.request().method()==='PATCH')
    await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click()
    assert.equal((await saved).status(),200);await page.getByRole('dialog').waitFor({state:'detached'})
    const after=(await request(page,'/api/v1/flashcards/due?deck_id='+deck.id)).data.cards.find(c=>c.id===card.id).schedule
    assert.deepEqual(after,before)
    await page.locator('.flashcard-flip').click()
    await page.locator('.flashcard-answer .katex').first().waitFor()
    assert.ok(await page.locator('.flashcard-answer .katex').count()>=2)
    assert.equal(await page.locator('.flashcard-answer a[href^="javascript:"]').count(),0)
    await page.screenshot({path:path.join(output,'current.png'),animations:'disabled'})
    const updated=consumer('updated',await download('updated'))
    assert.equal(updated.guid,first.guid);assert.equal(updated.card,first.card);assert.ok(updated.mtime>first.mtime)
    cloud.command('recycle');await cloud.event('RECYCLED')
    const restarted=consumer('updated',await download('restarted'))
    assert.equal(restarted.mtime,updated.mtime);assert.equal(restarted.card,first.card)
    const denied=await page.evaluate(async({account,deck})=>{
      const r=await fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(account)})
      const a=await r.json();return (await fetch('/api/v1/flashcards/export/anki/'+deck,{headers:{Authorization:'Bearer '+a.access_token}})).status
    },{account:ready.accounts[0],deck:deck.id})
    assert.equal(denied,404)
    report={at:new Date().toISOString(),passed:true,actual_native_edit:true,actual_native_math_ui:true,
      actual_default_anki_update:true,actual_schedule_identity_preserved:true,actual_restart_stable_mtime:true,
      actual_foreign_account_404:true,no_http_mocks:true,no_fake_clock:true,no_paid_call:true,
      actual_executable:process.env.KNOWALL_DESKTOP_EXECUTABLE,actual_backend:process.env.KNOWALL_TEST_BACKEND,first,updated,restarted}
  } catch(e) {if(page)await page.screenshot({path:path.join(output,'failed.png'),animations:'disabled'}).catch(()=>{});throw e}
  finally {
    try {if(client)await client.close()} finally {
      await cloud.close()
      assert.equal(path.dirname(path.resolve(data)),path.resolve(os.tmpdir()));assert.ok(path.basename(data).startsWith('knowall-anki-math-'))
      await fs.rm(data,{recursive:true,force:true})
    }
  }
  report.temporary_data_removed=true
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2))
  console.log('PASS actual native math edit/real cloud/Anki default/schedule/restart/private404/cleanup')
}
smoke().catch(e=>{console.error(e);process.exitCode=1})
