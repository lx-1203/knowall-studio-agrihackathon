import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.INTERACTIVE_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Private SQLite/JWT required; AI forbidden; response loss explicitly simulated')
for(const [kind,answer,ratio,mode] of [['fill_blank','甲;错',.5,'normal'],['short_answer','reliable',.75,'lost-200'],['term_definition','reliable',.75,'lost-read-outage'],['short_answer','reliable flow',1,'fractional']] as const){
test(`native rubric ${kind} ${mode} shows and recovers actual partial score`,async({page,request})=>{
  const Start=new Date().toISOString(),errors:string[]=[];let posts=0,reads=0
  page.on('pageerror',e=>errors.push(e.message));await request.post(ready.BackendURL+'/__test/reset')
  await request.post(ready.BackendURL+'/__test/rubric',{data:{kind}});await stubBackend(page)
  if(mode==='fractional')await request.post(ready.BackendURL+'/__test/update',{data:{scoring_points:[{point:'可靠性',keywords:['reliable'],score:.3},{point:'流控',keywords:['flow'],score:.1}]}})
  await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'rubric-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
  const state=async()=>(await request.get(ready.BackendURL+'/__test/state')).json()
  await page.route('**/api/v1/quiz/interactive/**',async r=>{
    const url=ready.BackendURL+new URL(r.request().url()).pathname+new URL(r.request().url()).search
    if(url.endsWith('/submit')){
      posts++;const response=await r.fetch({url})
      if(posts===1&&(mode==='lost-200'||mode==='lost-read-outage')){expect(response.status()).toBe(200);expect((await state()).AnswerRecords).toHaveLength(1);await r.abort('failed');return}
      if(posts===2&&mode==='lost-read-outage')expect(response.status()).toBe(409)
      await r.fulfill({response});return
    }
    if(url.endsWith('/stats')){reads++;if(reads===1&&mode==='lost-read-outage'){await r.abort('failed');return}}
    await r.continue({url})
  })
  const begin=page.waitForResponse(r=>r.url().includes('/quiz/interactive/start'))
  await page.goto('/quiz/interactive/sum');const initial=await(await begin).json(),sid=initial.session_id
  const q=initial.questions.find((q:any)=>q.id==='q1');for(const key of ['blanks','scoring_points','correct_answer','analysis'])expect(q).not.toHaveProperty(key)
  await expect(page.getByText('交互规则题：请回答问题',{exact:true})).toBeVisible()
  if(kind==='fill_blank')await expect(page.getByText('多空答案请按顺序用分号分隔。',{exact:true})).toBeVisible()
  // Update SQL rules after the page displayed the old question: old session must stay frozen.
  await request.post(ready.BackendURL+'/__test/update',{data:kind==='fill_blank'?{correct_answer:'新答案',blanks:[{index:1,answer:'错'},{index:2,answer:'甲'}]}:{correct_answer:'新答案',scoring_points:[{point:'新权重',keywords:['flow'],score:3},{point:'旧词',keywords:['reliable'],score:1}]}})
  const input=page.getByRole('textbox');await input.fill(answer);await page.getByRole('button',{name:'提交答案',exact:true}).click()
  if(mode==='lost-read-outage'){
    await expect(page.getByText(/提交失败:/)).toBeVisible();await expect(input).toHaveValue(answer)
    await input.fill('新输入不会替换已提交答案');await page.getByRole('button',{name:'提交答案',exact:true}).click()
  }
  const percent=`得分比例: ${ratio*100}%`
  await expect(page.getByText(percent,{exact:true})).toBeVisible();await expect(input).toHaveValue(answer);await expect(input).toBeDisabled()
  const First=await state();expect(First.AnswerRecords).toHaveLength(1);expect(First.AnswerRecords[0].Answer).toBe(answer)
  const headers={Authorization:'Bearer '+ready.Tokens.A}
  const foreign=await request.get(ready.BackendURL+`/api/v1/quiz/interactive/session/${sid}/stats`,{headers:{Authorization:'Bearer '+ready.Tokens.B}});expect(foreign.status()).toBe(404)
  const stats=await(await request.get(ready.BackendURL+`/api/v1/quiz/interactive/session/${sid}/stats`,{headers})).json()
  expect(stats.questions_detail[0].partial_score).toBe(ratio);expect(stats.questions_detail[0].correct_answer).toBe(kind==='fill_blank'?'甲;乙':'reliable flow')
  expect(stats.questions_detail[0].time_spent_ms).toBe(First.AnswerRecords[0].Time)
  await page.screenshot({path:path.join(path.dirname(file!),`real-${kind}-${mode}-partial.png`)})
  await expect(page.getByText('原生交互第2题',{exact:true})).toBeVisible();await page.getByRole('radio',{name:'A. 甲'}).check()
  await expect(page.getByText('答题完成！',{exact:true})).toBeVisible();await expect(page.getByText(percent,{exact:true})).toBeVisible()
  const Final=await state();expect(Final.AnswerRecords).toHaveLength(2);expect(Final.AIRequests).toHaveLength(0);expect(errors).toEqual([])
  expect(posts).toBe(mode==='lost-read-outage'?3:2);expect(reads).toBe(mode==='normal'||mode==='fractional'?0:mode==='lost-200'?1:2)
  await page.screenshot({path:path.join(path.dirname(file!),`real-${kind}-${mode}-recap.png`)})
  writeFileSync(path.join(path.dirname(file!),`real-browser-${kind}-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),JWTValidated:true,FaultSimulation:mode,Posts:posts,RecoveryReads:reads,First,Final,PageErrors:errors,SourceHashes:ready.SourceHashes},null,2))
})}
