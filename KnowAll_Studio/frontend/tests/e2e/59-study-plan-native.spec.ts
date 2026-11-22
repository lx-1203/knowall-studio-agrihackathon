import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.STUDY_PLAN_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Private SQL/JWT/FK backend required; failures explicitly injected, no AI')
for(const mode of ['create-inflight','goal-inflight','commit-retry'])test(`native plan ${mode}`,async({page,request})=>{
 const Start=new Date().toISOString(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await request.post(ready.BackendURL+'/__test/reset');await stubBackend(page)
 await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'native-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
 await page.route('**/api/v1/study/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname}))
 const headers={Authorization:'Bearer '+ready.Tokens.A},bob={Authorization:'Bearer '+ready.Tokens.B},base=ready.BackendURL+'/api/v1/study',getState=async()=>(await request.get(ready.BackendURL+'/__test/state')).json()
 expect((await request.get(base+'/plans/planA',{headers:bob})).status()).toBe(404)
 expect((await request.put(base+'/goals/goalA/toggle',{headers:bob})).status()).toBe(404)
 await page.goto('/study');await expect(page.getByRole('row').filter({hasText:'原生A计划'})).toBeVisible()
 let before:any,committed=false,release!:()=>void;const gate=new Promise<void>(r=>release=r)
 const newPlan=async()=>{await page.getByRole('button',{name:/New Plan/}).click();await page.getByPlaceholder('Plan name').fill('实际新创建');await page.getByPlaceholder('Goals (one per line)').fill('实际新目标');await page.getByRole('dialog').getByRole('button',{name:/^(?:OK|确\s*定)$/}).click()}
 if(mode==='commit-retry'){
  await request.post(ready.BackendURL+'/__test/fail-once');await newPlan();await expect(page.getByText('Failed to create plan',{exact:true})).toBeVisible();await expect(page.getByPlaceholder('Plan name')).toHaveValue('实际新创建');before=await getState();expect(before.Rows.Plans).toHaveLength(2);expect(before.Rows.Goals).toHaveLength(2)
  await page.getByRole('dialog').getByRole('button',{name:/(?:OK|确\s*定)$/}).click();await expect(page.getByRole('dialog')).toBeHidden();await expect(page.getByRole('row').filter({hasText:'实际新创建'})).toBeVisible()
 }else{
  const endpoint=mode==='goal-inflight'?'**/api/v1/study/goals/goalA/toggle':'**/api/v1/study/plans'
  await page.route(endpoint,async r=>{if(r.request().method()==='GET')return r.continue({url:base+'/plans'});const response=await r.fetch({url:ready.BackendURL+new URL(r.request().url()).pathname});expect(response.status()).toBe(200);committed=true;await gate;await r.fulfill({response})})
  if(mode==='goal-inflight'){await page.getByRole('row').filter({hasText:'原生A计划'}).click();await page.getByRole('checkbox').click()}else await newPlan()
  await expect.poll(()=>committed).toBe(true);before=await getState();expect(before.Rows.Plans).toHaveLength(mode==='create-inflight'?3:2)
  await page.evaluate(async({token})=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login(token,{id:'B',username:'native-B',email:'B@fixture.invalid'} as any)},{token:ready.Tokens.B})
  try{await expect(page.getByRole('row').filter({hasText:'原生B计划'})).toBeVisible();await expect(page.getByRole('dialog')).toBeHidden()}finally{release()}
  await page.waitForTimeout(150);await expect(page.getByText('Plan created',{exact:true})).toBeHidden();await expect(page.getByRole('row').filter({hasText:'实际新创建'})).toBeHidden();await expect(page.getByText('原生A目标',{exact:true})).toBeHidden()
  await page.getByRole('row').filter({hasText:'原生B计划'}).click();await expect(page.getByText('原生B目标',{exact:true})).toBeVisible();await expect(page.getByRole('checkbox')).not.toBeChecked()
 }
 const Final=await getState();expect(Final.AIRequests).toEqual([]);expect(Final.SQLiteForeignKeys).toBe(1);expect(errors).toEqual([])
 if(mode==='goal-inflight'){expect(Final.Rows.Goals.find((g:any)=>g.ID==='goalA').Completed).toBe(true);expect(Final.Rows.Plans.find((p:any)=>p.ID==='planA').Progress).toBe(100)}
 else {const saved=Final.Rows.Plans.filter((p:any)=>p.Name==='实际新创建');expect(saved).toHaveLength(1);expect(saved[0].User).toBe('A');expect(Final.Rows.Goals.filter((g:any)=>g.Plan===saved[0].ID)).toHaveLength(1)}
 expect(Final.CommitFailures).toBe(mode==='commit-retry'?1:0)
 writeFileSync(path.join(path.dirname(file!),`real-browser-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),FixtureFaults:true,ExternalAI:false,Mode:mode,Before:before,Final,PageErrors:errors},null,2))
})
