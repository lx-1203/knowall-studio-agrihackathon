import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.VARIANT_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Requires private real SQLite/JWT backend; external AI adapter explicitly simulated')
for(const [kind,percentage,recover] of [['fill_blank',50,false],['short_answer',75,false],['term_definition',75,true]] as const){
test(`native error variant ${kind} persists own rubric and scores ${percentage}%`,async({page,request})=>{
  const Start=new Date().toISOString(),errors:string[]=[]
  page.on('pageerror',e=>errors.push(e.message))
  await request.post(ready.BackendURL+'/__test/reset',{data:{kind,bad:recover}})
  const state=async()=>(await request.get(ready.BackendURL+'/__test/state')).json()
  const foreign=await request.post(ready.BackendURL+'/api/v1/quiz/errors/error/variants',{headers:{Authorization:'Bearer '+ready.Tokens.B}})
  expect(foreign.status()).toBe(404);expect((await state()).ProviderCalls).toHaveLength(0)
  await stubBackend(page)
  await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'variant-A',email:'A@round.invalid'}))},{token:ready.Tokens.A})
  await page.route('**/api/v1/quiz/**',async r=>{
    const u=new URL(r.request().url());const response=await r.fetch({url:ready.BackendURL+u.pathname+u.search});await r.fulfill({response})
  })
  await page.goto('/quiz');await page.getByRole('tab',{name:/错题/}).click()
  await expect(page.getByText('原生错题：原元素组合',{exact:true})).toBeVisible()
  const variants=page.getByRole('button',{name:/变式题$/})
  if(recover){
    const failed=page.waitForResponse(r=>r.url().includes('/errors/error/variants'))
    await variants.click();expect((await failed).status()).toBe(502)
    await expect(page.getByText('变式题生成失败',{exact:true})).toBeVisible()
    const bad=await state();expect(bad.Caches).toHaveLength(0);expect(bad.Questions).toHaveLength(1);expect(bad.Errors[0].generated).toBe(false)
    expect(bad.Logs).toEqual([{success:false,input:7,output:11}])
    await request.post(ready.BackendURL+'/__test/good')
  }
  const assembled=page.waitForResponse(r=>r.url().includes('/quiz/exam/create'))
  const produced=page.waitForResponse(r=>r.url().includes('/errors/error/variants'))
  await variants.click();const generation=await produced;expect(generation.status(),await generation.text()).toBe(200)
  const paper=await(await assembled).json()
  expect(paper.questions).toHaveLength(1)
  const q=paper.questions[0];expect(q.question_type).toBe(kind)
  if(kind==='fill_blank'){
    expect(q.blanks.map((b:any)=>b.answer)).toEqual(['氢','氧'])
    await page.getByPlaceholder('第一元素').fill('氢');await page.getByPlaceholder('第二元素').fill('错')
    await expect(page.getByText('空1',{exact:true})).toBeVisible();await expect(page.getByText('空2',{exact:true})).toBeVisible()
  }else{
    expect(q.scoring_points.map((p:any)=>p.score)).toEqual([3,1])
    await page.locator('textarea').fill('reliable')
  }
  const submitted=page.waitForResponse(r=>r.url().includes('/quiz/exam/submit'))
  await page.getByRole('button',{name:'提交 (1/1)',exact:true}).click()
  const response=await submitted;expect(response.status()).toBe(200)
  const result=await response.json();expect(result.percentage).toBe(percentage);expect(result.details[0].partial_score).toBe(percentage/100)
  await expect(page.getByText(`得分: ${percentage===50?'2.5':'3.75'}/5 (${percentage}%)`,{exact:true})).toBeVisible()
  if(kind!=='fill_blank')await expect(page.getByText('要点得分: 3/4',{exact:true})).toBeVisible()
  const Final=await state();expect(Final.Questions).toHaveLength(2);expect(Final.Errors.some((e:any)=>e.id==='error'&&e.generated)).toBe(true)
  expect(Final.ProviderCalls).toHaveLength(recover?3:2);expect(Final.Quota).toEqual([{user:'A',used:recover?54:36}]);expect(errors).toEqual([])
  await page.screenshot({path:path.join(path.dirname(file!),`real-${kind}.png`)})
  writeFileSync(path.join(path.dirname(file!),`real-browser-${kind}.json`),JSON.stringify({Start,End:new Date().toISOString(),JWTValidated:true,ExternalAISimulated:true,RecoveredBadOutput:recover,Paper:paper,Result:result,Final,PageErrors:errors,SourceHashes:ready.SourceHashes},null,2))
})}
