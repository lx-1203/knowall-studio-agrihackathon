import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.STUDY_REMINDER_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Requires private actual SQL/JWT server; virtual time and injected faults explicitly marked')
for(const mode of ['daily-clock','commit-retry','committed-response-loss'])test(`native reminder ${mode}`,async({page,request})=>{
 const Start=new Date().toISOString(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 await request.post(ready.BackendURL+'/__test/reset');await stubBackend(page);await page.clock.install()
 await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'study-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
 await page.route('**/api/v1/study/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname}))
 const base=ready.BackendURL+'/api/v1/study',headers={Authorization:'Bearer '+ready.Tokens.A},bob={Authorization:'Bearer '+ready.Tokens.B}
 const created=await request.post(base+'/reminders',{headers,data:{message:'真实持久提醒',remind_at:'2026-10-09T08:00:01+08:00',repeat_daily:mode==='daily-clock'}});expect(created.status()).toBe(200);const rid=(await created.json()).reminder_id
 expect((await request.get(base+'/reminders/due',{headers:bob})).status()).toBe(200);expect((await(await request.get(base+'/reminders/due',{headers:bob})).json()).count).toBe(0)
 expect((await request.post(base+`/reminders/${rid}/read`,{headers:bob})).status()).toBe(404)
 await page.goto('/study');await expect(page.getByText('No pending reminders')).toBeVisible()
 await request.post(ready.BackendURL+'/__test/clock',{data:{At:'2026-10-09T00:00:01'}});await page.clock.runFor(30001);await expect(page.getByText('真实持久提醒',{exact:true})).toBeVisible()
 if(mode==='commit-retry')await request.post(ready.BackendURL+'/__test/fail-once')
 if(mode==='committed-response-loss')await page.route('**/api/v1/study/reminders/*/read',async r=>{const response=await r.fetch({url:base+`/reminders/${rid}/read`});expect(response.status()).toBe(200);await r.abort('failed')})
 await page.getByRole('button',{name:'Dismiss'}).click()
 if(mode!=='daily-clock'){
  await expect(page.getByText('更新提醒失败',{exact:true})).toBeVisible();await expect(page.getByText('真实持久提醒',{exact:true})).toBeVisible()
  if(mode==='commit-retry')await page.getByRole('button',{name:'Dismiss'}).click()
  else await page.getByRole('button',{name:'刷新提醒',exact:true}).click()
 }
 await expect(page.getByText('真实持久提醒',{exact:true})).toBeHidden()
 if(mode==='daily-clock'){
  const middle=await(await request.get(ready.BackendURL+'/__test/state')).json();expect(middle.Rows[0].At).toBe('2026-10-10T00:00:01');expect(middle.Rows[0].Read).toBe(false)
  await request.post(ready.BackendURL+'/__test/clock',{data:{At:'2026-10-10T00:00:01'}});await page.clock.runFor(30001);await expect(page.getByText('真实持久提醒',{exact:true})).toBeVisible()
 }
 const Final=await(await request.get(ready.BackendURL+'/__test/state')).json();expect(Final.SQLiteForeignKeys).toBe(1);expect(Final.AIRequests).toEqual([]);expect(Final.Rows).toHaveLength(1);expect(Final.Rows[0].User).toBe('A');expect(Final.Rows[0].Read).toBe(mode!=='daily-clock')
 const posts=Final.BackendSamples.filter((s:any)=>s.Method==='POST'&&s.Status!==404&&s.Path.endsWith('/read'));expect(posts).toHaveLength(mode==='commit-retry'?2:1);expect(Final.CommitFailures).toBe(mode==='commit-retry'?1:0);expect(errors).toEqual([])
 writeFileSync(path.join(path.dirname(file!),`real-browser-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),VirtualClock:true,ExternalAI:false,Mode:mode,Final,PageErrors:errors},null,2))
})
