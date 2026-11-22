import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.STUDY_REMINDER_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Requires isolated actual JWT/SQLite/HTTP; delayed response faults explicitly simulated')
const cases=[{mode:'aba',status:200},{mode:'aba',status:401},{mode:'aba',status:500},{mode:'refresh',status:500},{mode:'batched-aba',status:200},{mode:'same-token-login',status:401}]
for(const {mode,status} of cases)test(`native ${mode} late ${status} preserves current session and writes`,async({page,request})=>{
 const Start=new Date().toISOString(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 const base=ready.BackendURL+'/api/v1/study',headers={Authorization:'Bearer '+ready.Tokens.A},bob={Authorization:'Bearer '+ready.Tokens.B}
 expect((await request.post(ready.BackendURL+'/__test/reset')).ok()).toBe(true)
 await stubBackend(page)
 await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
 await page.route('**/api/v1/study/**',r=>r.continue({url:base+new URL(r.request().url()).pathname.slice('/api/v1/study'.length)}))
 const create=async(message:string,h=headers)=>{
  const response=await request.post(base+'/reminders',{headers:h,data:{message,remind_at:'2020-01-01T00:00:00Z',repeat_daily:false}})
  expect(response.status()).toBe(200);return (await response.json()).reminder_id as string
 }
 const old=await create('原生旧会话提醒');await create('原生B提醒',bob)
 expect((await request.post(base+`/reminders/${old}/read`,{headers:bob})).status()).toBe(404)
 await page.goto('/study');await expect(page.getByText('原生旧会话提醒',{exact:true})).toBeVisible()
 expect((await request.post(ready.BackendURL+'/__test/arm',{data:{Status:status}})).ok()).toBe(true)
 await page.getByRole('button',{name:'Dismiss'}).click()
 await expect.poll(async()=>{
  const s=await(await request.get(ready.BackendURL+'/__test/state')).json();return s.Samples.some((r:any)=>r.Held&&r.CommittedBeforeGate)
 }).toBe(true)
 const newID=await create('原生当前会话提醒')
 const token=mode==='refresh'?ready.Tokens.Anew:ready.Tokens.A
 const login=async(t:string,id:string)=>page.evaluate(async({t,id})=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login(t,{id,username:id,email:id+'@fixture.invalid'} as any)},{t,id})
 if(mode==='aba'){await login(ready.Tokens.B,'B');await expect(page.getByText('原生B提醒',{exact:true})).toBeVisible();await login(token,'A')}
 else if(mode==='batched-aba')await page.evaluate(async({a,b})=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login(b,{id:'B'} as any);useAuthStore.getState().login(a,{id:'A',username:'A',email:'A@fixture.invalid'} as any)},{a:token,b:ready.Tokens.B})
 else await login(token,'A')
 const lateResponse=page.waitForResponse(r=>r.url().includes(`/reminders/${old}/read`))
 try{
  await expect(page.getByText('原生当前会话提醒',{exact:true})).toBeVisible();await expect(page.getByText('原生B提醒',{exact:true})).toBeHidden()
  const response=page.waitForResponse(r=>r.url().includes(`/reminders/${newID}/read`))
  await page.getByRole('button',{name:'Dismiss'}).click();expect((await response).status()).toBe(200)
  await expect(page.getByText('No pending reminders')).toBeVisible()
 }finally{await request.post(ready.BackendURL+'/__test/release')}
 const late=await lateResponse;expect(late.status()).toBe(status);await late.finished()
 await expect.poll(async()=>{
  const s=await(await request.get(ready.BackendURL+'/__test/state')).json();return s.Samples.find((r:any)=>r.Held)?.Status
 }).toBe(status)
 await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))))
 await expect.poll(()=>page.evaluate(()=>localStorage.getItem('knowall_token'))).toBe(token)
 await expect(page.getByText('No pending reminders')).toBeVisible();await expect(page.getByText('更新提醒失败',{exact:true})).toBeHidden()
 const Final=await(await request.get(ready.BackendURL+'/__test/state')).json()
 expect(Final.SQLiteForeignKeys).toBe(1);expect(Final.AIRequests).toEqual([]);expect(errors).toEqual([])
 expect(Final.Rows.filter((r:any)=>r.User==='A').every((r:any)=>r.Read)).toBe(true)
 expect(Final.Rows.find((r:any)=>r.User==='B').Read).toBe(false)
 expect(Final.Samples.filter((r:any)=>r.Method==='POST'&&r.Path.endsWith('/read')&&r.SQLStatus===200)).toHaveLength(2)
 writeFileSync(path.join(path.dirname(file!),`real-browser-${mode}-${status}.json`),JSON.stringify({Start,End:new Date().toISOString(),Mode:mode,FaultStatus:status,ExternalAI:false,Final,PageErrors:errors},null,2))
})
test('actual invalid JWT returns 401 and current page logs out',async({page,request})=>{
 const Start=new Date().toISOString();await request.post(ready.BackendURL+'/__test/reset');await stubBackend(page)
 const headers={Authorization:'Bearer '+ready.Tokens.A}
 expect((await request.post(ready.BackendURL+'/api/v1/study/reminders',{headers,data:{message:'原生有效提醒',remind_at:'2020-01-01T00:00:00Z'}})).status()).toBe(200)
 await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'A'}))},{token:ready.Tokens.A})
 await page.route('**/api/v1/study/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname}))
 await page.goto('/study');await expect(page.getByText('原生有效提醒',{exact:true})).toBeVisible()
 await page.evaluate(async()=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('explicit-invalid-fixture-jwt',{id:'A'} as any)})
 await expect.poll(()=>page.evaluate(()=>localStorage.getItem('knowall_token'))).toBeNull()
 const Final=await(await request.get(ready.BackendURL+'/__test/state')).json();expect(Final.Samples.some((r:any)=>r.SQLStatus===401)).toBe(true);expect(Final.AIRequests).toEqual([])
 writeFileSync(path.join(path.dirname(file!),'real-browser-current-401.json'),JSON.stringify({Start,End:new Date().toISOString(),Final,ExternalAI:false},null,2))
})
