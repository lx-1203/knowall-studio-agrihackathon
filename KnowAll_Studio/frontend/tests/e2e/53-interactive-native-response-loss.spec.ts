import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.INTERACTIVE_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Private real SQLite/JWT required; response fault explicitly simulated, AI forbidden')
for(const mode of ['lost-200','lost-then-read-outage','commit-failure']){
test(`real interactive ${mode} restores one truthful SQL record`,async({page,request})=>{
  const Start=new Date().toISOString(),errors:string[]=[];let posts=0,reads=0
  page.on('pageerror',e=>errors.push(e.message));await request.post(ready.BackendURL+'/__test/reset');await stubBackend(page)
  await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'loss-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
  const state=async()=>(await request.get(ready.BackendURL+'/__test/state')).json()
  await page.route('**/api/v1/quiz/interactive/**',async r=>{
    const url=ready.BackendURL+new URL(r.request().url()).pathname+new URL(r.request().url()).search
    if(url.endsWith('/submit')){
      posts++;const response=await r.fetch({url});if(posts===1&&mode!=='commit-failure'){
        expect(response.status()).toBe(200);expect((await state()).AnswerRecords).toHaveLength(1)
        await r.abort('failed');return // Explicit browser fault after actual handler/SQL completion.
      }
      if(posts===2&&mode==='lost-then-read-outage')expect(response.status()).toBe(409)
      await r.fulfill({response});return
    }
    if(url.endsWith('/stats')){reads++;if(reads===1&&mode==='lost-then-read-outage'){await r.abort('failed');return}}
    await r.continue({url})
  })
  const begin=page.waitForResponse(r=>r.url().includes('/quiz/interactive/start'))
  await page.goto('/quiz/interactive/sum');const session=(await(await begin).json()).session_id
  await expect(page.getByText('原生交互第1题',{exact:true})).toBeVisible()
  if(mode==='commit-failure')await request.post(ready.BackendURL+'/__test/fail-once')
  await page.getByRole('radio',{name:'A. 甲'}).check()
  if(mode!=='lost-200'){
    await expect(page.getByText(/提交失败:/)).toBeVisible()
    expect((await state()).AnswerRecords).toHaveLength(mode==='commit-failure'?0:1)
    if(mode==='lost-then-read-outage'){await request.post(ready.BackendURL+'/__test/update',{data:{correct_answer:'B',analysis:'更新后的新解析'}});await page.getByRole('radio',{name:'B. 乙'}).check()}
    else await page.getByRole('button',{name:'重新提交',exact:true}).click()
  }
  await expect(page.getByText('甲为正确答案',{exact:true})).toBeVisible();await expect(page.getByRole('radio',{name:'A. 甲'})).toBeChecked();await expect(page.getByRole('radio',{name:'A. 甲'})).toBeDisabled()
  const first=await state();expect(first.AnswerRecords).toHaveLength(1);expect(first.AnswerRecords[0].Answer).toBe('A')
  const headers={Authorization:'Bearer '+ready.Tokens.A},foreign={Authorization:'Bearer '+ready.Tokens.B}
  expect((await request.get(ready.BackendURL+`/api/v1/quiz/interactive/session/${session}/stats`,{headers:foreign})).status()).toBe(404)
  const recovered=await(await request.get(ready.BackendURL+`/api/v1/quiz/interactive/session/${session}/stats`,{headers})).json()
  expect(recovered.questions_detail).toHaveLength(1);expect(recovered.questions_detail[0].analysis).toBe('甲为正确答案');expect(recovered.questions_detail[0].time_spent_ms).toBe(first.AnswerRecords[0].Time)
  await expect(page.getByText('原生交互第2题',{exact:true})).toBeVisible();await page.getByRole('radio',{name:'A. 甲'}).check();await expect(page.getByText('正确率: 100.0% (2/2)',{exact:true})).toBeVisible()
  const Final=await state();expect(Final.AnswerRecords).toHaveLength(2);expect(Final.AIRequests).toHaveLength(0);expect(errors).toEqual([])
  expect(posts).toBe(mode==='lost-200'?2:3);expect(reads).toBe(mode==='lost-then-read-outage'?2:1)
  writeFileSync(path.join(path.dirname(file!),`real-browser-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),Simulation:true,ActualCommitBeforeBrowserFault:mode!=='commit-failure',JWTValidated:true,Posts:posts,RecoveryReads:reads,First:first,Final,PageErrors:errors},null,2))
})}
