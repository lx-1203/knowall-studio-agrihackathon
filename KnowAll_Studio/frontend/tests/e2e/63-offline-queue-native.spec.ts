import { test,expect } from '@playwright/test'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.OFFLINE_QUEUE_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Private signed JWT/SQLite and real Vite WS proxy required; external AI forbidden')
const op=(user:string,doc:string,value:string)=>({type:'operation',doc_id:doc,user_id:user,version:0,timestamp:1,data:{operation:'insert',position:0,value,path:['text']}})
for(const mode of ['B-initial','A-B-A','other-room-reload'])test(`native offline ${mode} respects signed owner and room`,async({page,request})=>{
 const Start=new Date().toISOString(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await request.post(ready.BackendURL+'/__test/reset');await page.route('**/api/**',r=>r.fulfill({json:[]}));const clock=new Date('2026-10-09T15:00:00Z');await page.clock.install({time:clock});await page.clock.pauseAt(clock)
 const items=mode==='other-room-reload'?[op('B','space_other','OTHER')]:[op('A','space_default','A-EDIT'),op('B','space_other','OTHER'),op('B','space_default','B-EDIT')]
 await page.addInitScript(({tokens,items,user})=>{localStorage.setItem('knowall_token',tokens[user]);localStorage.setItem('knowall_user',JSON.stringify({id:user,username:user,email:user+'@fixture.invalid'}));if(!sessionStorage.getItem('nativeSeeded')){localStorage.setItem('knowall_offline_queue',JSON.stringify(items));sessionStorage.setItem('nativeSeeded','yes')}},{tokens:ready.Tokens,items,user:mode==='A-B-A'?'A':'B'})
 const getState=async()=>(await request.get(ready.BackendURL+'/__test/state')).json(),remaining=async()=>page.evaluate(()=>JSON.parse(localStorage.getItem('knowall_offline_queue')||'[]'))
 const login=async(user:string)=>page.evaluate(async({user,token})=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login(token,{id:user,username:user,email:user+'@fixture.invalid'} as any)},{user,token:ready.Tokens[user]})
 await page.goto('/');await expect.poll(async()=>((await getState()).Wire.length)).toBe(1)
 if(mode==='A-B-A'){await login('B');await expect.poll(async()=>((await getState()).Wire.length)).toBe(2)}
 await page.clock.runFor(6000)
 if(mode==='other-room-reload'){
  await expect.poll(async()=>((await getState()).Ops.length)).toBe(0);expect((await remaining()).map((i:any)=>i.data.value)).toEqual(['OTHER']);await page.reload();await expect.poll(async()=>((await getState()).Wire.length)).toBe(2);await page.clock.runFor(6000);expect((await remaining()).map((i:any)=>i.data.value)).toEqual(['OTHER'])
 }else{
  await expect.poll(async()=>((await getState()).Ops.length)).toBe(1);const first=await getState();expect(first.Ops[0]).toMatchObject({User:'B',Room:'space_default',Version:1,Data:{value:'B-EDIT'}});expect((await remaining()).map((i:any)=>i.data.value)).toEqual(['A-EDIT','OTHER'])
  if(mode==='A-B-A'){await login('A');await expect.poll(async()=>((await getState()).Wire.length)).toBe(3);await page.clock.runFor(6000);await expect.poll(async()=>((await getState()).Ops.length)).toBe(2);expect((await remaining()).map((i:any)=>i.data.value)).toEqual(['OTHER'])}
 }
 const Final=await getState();expect(Final.AIRequests).toEqual([]);expect(errors).toEqual([]);expect(Final.Ops.some((o:any)=>o.Room==='space_other')).toBe(false)
 if(mode==='A-B-A')expect(Final.Ops[1]).toMatchObject({User:'A',Version:2,Data:{value:'A-EDIT'}})
 const operations=Final.Wire.flatMap((w:any)=>w.Received.filter((m:any)=>m.type==='operation').map((m:any)=>({ConnectionUser:w.User,MessageUser:m.user_id,Data:m.data})))
 expect(operations.every((o:any)=>o.ConnectionUser===o.MessageUser)).toBe(true);expect(operations.map((o:any)=>o.Data.value)).toEqual(mode==='B-initial'?['B-EDIT']:mode==='A-B-A'?['B-EDIT','A-EDIT']:[])
 writeFileSync(path.join(path.dirname(file!),`real-wire-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),BrowserClockVirtual:true,Transport:'actual WebSocket through private Vite proxy',ExternalAI:false,Final,Remaining:await remaining(),PageErrors:errors},null,2))
})
