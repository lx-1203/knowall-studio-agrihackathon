import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.QUESTION_DELETE_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Private actual SQL/JWT/FK server required; faults explicitly injected and no AI')
for(const mode of ['answered-protection','commit-retry','late-reference']){
test(`native bank ${mode} preserves learning records and sources transactionally`,async({page,request})=>{
  const Start=new Date().toISOString(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
  await request.post(ready.BackendURL+'/__test/reset');await stubBackend(page)
  await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'delete-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
  await page.route('**/api/v1/quiz/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname+new URL(r.request().url()).search}))
  const headers={Authorization:'Bearer '+ready.Tokens.A},base=ready.BackendURL+'/api/v1/quiz',state=async()=>(await request.get(ready.BackendURL+'/__test/state')).json()
  const start=await(await request.get(base+'/interactive/start?summary_id=sum&count=3',{headers})).json()
  if(mode==='answered-protection')expect((await request.post(base+'/interactive/submit',{headers,data:{session_id:start.session_id,question_id:'q1',user_answer:'B',time_spent_ms:41}})).status()).toBe(200)
  else await request.post(ready.BackendURL+(mode==='commit-retry'?'/__test/bank-fail-once':'/__test/bank-reference-once'))
  await page.goto('/quiz');await page.getByRole('tab',{name:/题库/}).click();await expect(page.getByText('原生交互第2题',{exact:true})).toBeVisible()
  const check=(id:string)=>page.locator(`tr[data-row-key="${id}"]`).getByRole('checkbox')
  await check('q2').check();if(mode!=='commit-retry')await check('q1').check()
  await page.getByRole('button',{name:/删除 \([12]\)$/}).click();const dialog=page.getByRole('dialog')
  const rejected=page.waitForResponse(r=>r.url().endsWith('/quiz/bank/delete-batch'));await dialog.getByRole('button',{name:'确认删除',exact:true}).click();expect((await rejected).status()).toBe(mode==='commit-retry'?500:409)
  await expect(page.getByText(mode==='commit-retry'?'Explicit fixture-only SQL commit outage':'题目已有关联答题、错题或课堂记录，暂不能删除',{exact:true})).toBeVisible();await expect(dialog).toBeVisible()
  const rejectedState=await state();expect(rejectedState.QuestionBank).toHaveLength(3);expect(rejectedState.Coverage).toHaveLength(3);expect(rejectedState.AnswerRecords).toHaveLength(mode==='commit-retry'?0:1)
  if(mode==='commit-retry')await dialog.getByRole('button',{name:/确认删除$/}).click()
  else {await dialog.getByRole('button',{name:/取\s*消/}).click();await check('q1').uncheck();await page.getByRole('button',{name:/删除 \(1\)$/}).click();await page.getByRole('dialog').getByRole('button',{name:'确认删除',exact:true}).click()}
  await expect(page.getByText('已删除 1 题',{exact:true})).toBeVisible();await expect(page.getByText('原生交互第2题',{exact:true})).toBeHidden();await expect(page.getByText('原生交互第1题',{exact:true})).toBeVisible()
  expect((await request.post(base+'/interactive/submit',{headers,data:{session_id:start.session_id,question_id:'q2',user_answer:'A'}})).status()).toBe(404)
  expect((await request.post(base+'/interactive/submit',{headers,data:{session_id:start.session_id,question_id:'q3',user_answer:'A'}})).status()).toBe(200)
  const fresh=await(await request.get(base+'/interactive/start?summary_id=sum&count=3',{headers})).json();expect(fresh.questions.map((q:any)=>q.id)).toEqual(['q1','q3'])
  const Final=await state();expect(Final.Coverage.map((c:any)=>c.ResourceID).sort()).toEqual(['q1','q3']);expect(Final.QuestionBank.map((q:any)=>q.ID).sort()).toEqual(['q1','q3']);expect(Final.AnswerRecords).toHaveLength(mode==='commit-retry'?1:2);expect(Final.AIRequests).toHaveLength(0);expect(errors).toEqual([])
  writeFileSync(path.join(path.dirname(file!),`real-browser-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),Simulation:true,JWTUsedForInteractive:true,BankLegacyUnauthenticated:true,Mode:mode,Rejected:rejectedState,Final,PageErrors:errors},null,2))
})}
