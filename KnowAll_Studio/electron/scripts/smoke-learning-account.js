const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http'), assert = require('node:assert/strict'), crypto = require('node:crypto')
async function main() {
  const root = path.resolve(__dirname, '..'), out = path.resolve(root, '../docs/open-source-integration')
  const exe = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round17/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-learning-account-'))
  const records = [], held = []
  const question = id => ({ id: `${id}-q`, question_type: 'single_choice', question_text: `${id}-private-question`, options: [{label:'A',text:'正确'},{label:'B',text:'其他'}], answer:'A', analysis:'来源', difficulty:'medium', tags:[] })
  const exam = id => ({paper_id:`${id}-paper`,title:`${id}-private-exam`,question_count:1,questions:[question(id)]})
  const cards = id => [0,1].map(i=>({id:`${id}-card-${i}`,card_type:'qa',front:`${id}-private-card-${i}`,back:`${id}-private-answer-${i}`,tags:[]}))
  const passage = id => ({id:`${id}-passage`,title:`${id}-private-passage`,content:`${id}账号原文`,category:'custom',difficulty:1,tags:[]})
  let client, page, report, holdPath = null
  const server = http.createServer((req,res)=>{
    const uri = new URL(req.url,'http://localhost').pathname
    const account = req.headers.authorization?.replace('Bearer ','').split('-')[0]
    const record = {path:uri,account,method:req.method,cancelled:false};records.push(record)
    res.setHeader('Content-Type','application/json');res.on('close',()=>{if(!res.writableEnded)record.cancelled=true})
    const chunks=[];req.on('data',b=>chunks.push(b));req.on('end',()=>{
      const body=Buffer.concat(chunks).toString(), respond=value=>res.end(JSON.stringify(value))
      if(uri==='/health')return respond({status:'ok'})
      if(uri==='/api/v1/auth/local-session')return respond({enabled:false})
      if(uri==='/api/v1/oauth/providers')return respond({providers:[]})
      if(uri==='/api/v1/auth/login'){const id=JSON.parse(body).username;return respond({access_token:`${id}-token`,user:{id,username:id,email:`${id}@example.test`,is_active:true}})}
      if(uri==='/api/v1/auth/me')return respond({id:account,username:account,email:`${account}@example.test`,is_active:true})
      let result=[]
      if(uri==='/api/v1/quiz/questions')result=[question(account)]
      if(uri==='/api/v1/quiz/exam/create')result=exam(account)
      if(uri==='/api/v1/quiz/exam/submit')result={paper_id:`${account}-paper`,score:5,total:1,correct:1,percentage:100,details:[{question_id:`${account}-q`,is_correct:true,user_answer:'A',correct_answer:'A'}]}
      if(uri==='/api/v1/flashcards/due')result={cards:cards(account)}
      if(uri==='/api/v1/flashcards/review')result={next_review_at:'2026-10-10'}
      if(uri==='/api/v1/recitation/passages')result={passages:[passage(account)]}
      if(uri==='/api/v1/recitation/stats')result={total_passages:1,total_records:0,avg_accuracy:0,mastered:0}
      if(uri==='/api/v1/recitation/due')result={due_items:[]}
      if(uri==='/api/v1/recitation/hide-plan')result={segments:[{text:`${account}账号遮字`,hidden:false}],total_chars:6,hidden_chars:0}
      if(uri==='/api/v1/recitation/dictation/submit')result={record_id:`${account}-record`,accuracy:1,correct_chars:6,missing_chars:0,extra_chars:0,original_length:6,input_length:6,diffs:[]}
      if(uri.startsWith('/api/v1/quiz/')||uri.startsWith('/api/v1/flashcards/')||uri.startsWith('/api/v1/recitation/'))assert.ok(['A','B'].includes(account),'Bearer required on private learning requests')
      if(uri===holdPath&&account==='A'){held.push({record,release:()=>respond(result)});return}
      respond(result)
    })
  })
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
  try {
    const env={...process.env,KNOWALL_DESKTOP_DATA_DIR:data};delete env.KNOWALL_SERVER_URL;delete env.ELECTRON_RUN_AS_NODE
    client=await _electron.launch({executablePath:exe,env,timeout:30000})
    const settings=await client.firstWindow();await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened=client.waitForEvent('window');await settings.locator('#submit').click();page=await opened;await page.setViewportSize({width:1600,height:950})
    const login=async id=>{await page.getByLabel('账号',{exact:true}).fill(id);await page.getByLabel('密码',{exact:true}).fill('temporary-password');await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.locator('.product-account')).toHaveText(id)}
    const switchTo=async id=>{await page.getByRole('button',{name:'登出',exact:true}).click();await login(id)}
    const startExam=async id=>{await page.getByRole('tab',{name:/题库/}).click();await page.getByRole('button',{name:/一键刷题/}).click();await expect(page.getByText(`${id}-private-exam`,{exact:true})).toBeVisible()}
    const startPassage=async(id,mode)=>{await page.getByRole('row').filter({hasText:`${id}-private-passage`}).getByRole('button',{name:mode==='hide'?/遮字背诵$/:/默\s*写$/}).click()}
    const heldFor=async uri=>{await expect.poll(()=>held.some(h=>h.record.path===uri)).toBe(true)}
    const release=async uri=>{await expect.poll(()=>records.some(r=>r.path===uri&&r.account==='A'&&r.cancelled)).toBe(true);held.filter(h=>h.record.path===uri).forEach(h=>h.release());holdPath=null}
    await login('A');await page.getByRole('link',{name:'全部题库',exact:true}).click();await startExam('A')
    await switchTo('B');await page.getByRole('tab',{name:/答题/}).click();await expect(page.getByText('暂无进行中的试卷',{exact:true})).toBeVisible();await expect(page.getByText('A-private-exam',{exact:true})).toBeHidden()
    await switchTo('A');holdPath='/api/v1/quiz/exam/create';await page.getByRole('tab',{name:/题库/}).click();await page.getByRole('button',{name:/一键刷题/}).click();await heldFor(holdPath)
    await switchTo('B');await startExam('B');await release('/api/v1/quiz/exam/create');await expect(page.getByText('B-private-exam',{exact:true})).toBeVisible();await expect(page.getByText('A-private-exam',{exact:true})).toBeHidden()
    await page.screenshot({path:path.join(out,'round-17-packaged-B-exam.png'),animations:'disabled'})
    await switchTo('A');await page.getByRole('link',{name:'统一复习',exact:true}).click();await page.getByText('A-private-card-0',{exact:true}).click()
    holdPath='/api/v1/flashcards/review';await page.getByRole('button',{name:'正常回忆 (3)',exact:true}).click();await heldFor(holdPath)
    await expect(page.getByRole('button',{name:'正常回忆 (3)',exact:true})).toBeDisabled()
    await switchTo('B');await expect(page.getByText('B-private-card-0',{exact:true})).toBeVisible();await release('/api/v1/flashcards/review');await expect(page.getByText('B-private-card-0',{exact:true})).toBeVisible();await expect(page.getByText('A-private-answer-0',{exact:true})).toBeHidden()
    await page.screenshot({path:path.join(out,'round-17-packaged-B-card.png'),animations:'disabled'})
    await switchTo('A');await page.getByRole('menuitem',{name:'更多工具',exact:true}).click();await page.getByRole('link',{name:'背诵练习',exact:true}).click()
    holdPath='/api/v1/recitation/hide-plan';await startPassage('A','hide');await heldFor(holdPath)
    await switchTo('B');await startPassage('B','hide');await expect(page.getByText('B账号遮字',{exact:true})).toBeVisible();await release('/api/v1/recitation/hide-plan');await expect(page.getByText('A账号遮字',{exact:true})).toBeHidden()
    await page.screenshot({path:path.join(out,'round-17-packaged-B-hide.png'),animations:'disabled'})
    await page.getByRole('button',{name:'← 返回列表',exact:true}).click();await switchTo('A');await startPassage('A','dictation');await page.getByPlaceholder('在此输入你的默写内容...').fill('A秘密输入')
    holdPath='/api/v1/recitation/dictation/submit';await page.getByRole('button',{name:/^检\s*查$/}).click();await heldFor(holdPath)
    await switchTo('B');await startPassage('B','dictation');await release('/api/v1/recitation/dictation/submit');await expect(page.getByPlaceholder('在此输入你的默写内容...')).toHaveValue('');await expect(page.getByText('A-private-passage',{exact:true})).toBeHidden()
    await page.screenshot({path:path.join(out,'round-17-packaged-B-dictation.png'),animations:'disabled'})
    const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),resources=path.join(path.dirname(exe),'resources')
    const notices=JSON.parse(await fs.readFile(path.join(out,'round-17-runtime-notices.json'),'utf8'));let checked=0
    for(const n of notices)for(const f of n.original_notices){assert.equal(sha(await fs.readFile(path.join(resources,'frontend/third-party',n.name.replace('@','').replace('/','-'),f.file))),f.sha256);checked++}
    report={at:new Date().toISOString(),passed:true,executable:exe,app_asar_sha256:sha(await fs.readFile(path.join(resources,'app.asar'))),frontend_index_sha256:sha(await fs.readFile(path.join(resources,'frontend/index.html'))),records,original_notice_files_checked:checked,observations:{real_logout_B_no_A_exam:true,old_exam_create_cancelled:true,old_rating_cancelled_no_B_advance:true,old_hide_plan_cancelled_no_B_overwrite:true,old_dictation_cancelled_no_B_result:true,rating_pending_disabled:true},scope:'Current real packaged Electron/gateway/local deterministic HTTP; real temporary logins; no production backend/paid calls; cloud workers separately verified'}
  } catch(error){await fs.writeFile(path.join(out,'round-17-packaged-learning-diagnostics.json'),JSON.stringify({url:page?.url(),snapshot:page?await page.locator('body').ariaSnapshot():'',records},null,2));throw error}
  finally{if(client)await client.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));assert.equal(path.dirname(data),os.tmpdir());assert.ok(path.basename(data).startsWith('knowall-learning-account-'));await fs.rm(data,{recursive:true,force:true})}
  report.temporary_data_removed=true;await fs.writeFile(path.join(out,'round-17-packaged-learning-account.json'),JSON.stringify(report,null,2));console.log('PASS current packaged three learning flows/account reset/old request native cancellation/no stale global writes/Bearer/notices/cleanup')
}
main().catch(error=>{console.error(error);process.exitCode=1})
