import {test,expect} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const output=path.resolve(process.cwd(),'../docs/test-round-07b2')
const ready=JSON.parse(fs.readFileSync(path.join(output,'native-ready.json'),'utf8'))
const backend=ready.BackendURL,wire=backend.replace(/^http/,'ws')+'/ws/sync'
test.beforeEach(async({page,request})=>{
 await request.post(backend+'/__test/reset')
 await page.addInitScript(token=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'A',email:'a@test.invalid'}))},ready.Tokens.A)
 await page.route('**/api/**',r=>r.fulfill({json:[]}))
 await page.routeWebSocket(/^ws:\/\/127\.0\.0\.1:5318\//,()=>{})
 await page.goto('/')
})
async function create(page:any,token=ready.Tokens.A,room='space_default'){
 await page.evaluate(async({wire,token,room})=>{
  const {RealtimeSyncClient}=await import('/src/services/RealtimeSyncClient.ts')
  const sent:any[]=[],received:any[]=[],errors:any[]=[]
  const Native=window.WebSocket
  class Observed extends Native{send(raw:any){sent.push({Time:Date.now(),Message:JSON.parse(raw)});super.send(raw)}}
  ;(window as any).WebSocket=Observed
  const client=new RealtimeSyncClient({url:wire,docId:room,userId:'A',userName:'untrusted',token,onError:e=>errors.push(e)})
  for(const type of ['ack','sync_diff','sync_full','presence','error'])client.on(type,m=>received.push({Time:Date.now(),Message:m}))
  ;(window as any).native={client,sent,received,errors};client.connect();client.connect()
 },{wire,token,room})
}
async function state(request:any){return (await request.get(backend+'/__test/state')).json()}
async function save(name:string,page:any,request:any){
 await page.evaluate(()=>(window as any).native.client.disconnect())
 await expect.poll(async()=>{const s=await state(request);return s.Wire.every((r:any)=>r.End)}).toBeTruthy()
 const s=await state(request),client=await page.evaluate(()=>{const f=(window as any).native;return {Sent:f.sent,Received:f.received,Errors:f.errors,Ready:f.client.isConnected(),Version:f.client.getVersion()}})
 fs.writeFileSync(path.join(output,'real-wire-'+name+'.json'),JSON.stringify({...s,Client:client,SourceHashes:ready.SourceHashes,Time:new Date().toISOString(),RealWebSocket:true,ExternalAI:false},null,2))
}
test('native repeated connect opens one signed connection',async({page,request})=>{
 await create(page);await expect.poll(()=>page.evaluate(()=>(window as any).native.client.isConnected())).toBe(true)
 await page.evaluate(()=>(window as any).native.client.connect())
 const s=await state(request);expect(s.Wire).toHaveLength(1);expect(s.Wire[0].User).toBe('A');expect(s.Ops).toEqual([])
 await save('idempotent',page,request)
})
test('native business send waits for held recovery ACK then commits once',async({page,request})=>{
 await request.post(backend+'/__test/hold');await create(page)
 await expect.poll(async()=>(await state(request)).Held).toBe(true)
 const before=await page.evaluate(()=>{const f=(window as any).native;f.client.sendOperation({operation:'insert',position:0,value:'held-A',path:['text']});return {Ready:f.client.isConnected(),Types:f.sent.map((r:any)=>r.Message.type)}})
 expect(before).toEqual({Ready:false,Types:['reconnect']});expect((await state(request)).Ops).toEqual([])
 await request.post(backend+'/__test/release');await expect.poll(async()=>(await state(request)).Ops.length).toBe(1)
 await expect.poll(()=>page.evaluate(()=>(window as any).native.client.getVersion())).toBe(1)
 const order=await page.evaluate(()=>{const f=(window as any).native;return {Ack:f.received.find((r:any)=>r.Message.type==='ack').Time,Op:f.sent.find((r:any)=>r.Message.type==='operation').Time}})
 expect(order.Op).toBeGreaterThanOrEqual(order.Ack);expect((await state(request)).Ops[0]).toMatchObject({User:'A',Version:1,Data:{value:'held-A'}})
 await save('held-ack',page,request)
})
test('native disconnect reconnect waits again and preserves queued edit',async({page,request})=>{
 await create(page);await expect.poll(()=>page.evaluate(()=>(window as any).native.client.isConnected())).toBe(true)
 await page.evaluate(()=>(window as any).native.client.sendOperation({operation:'insert',position:0,value:'A',path:['text']}))
 await expect.poll(()=>page.evaluate(()=>(window as any).native.client.getVersion())).toBe(1)
 await request.post(backend+'/__test/drop');await expect.poll(()=>page.evaluate(()=>(window as any).native.client.isConnected())).toBe(false)
 await request.post(backend+'/__test/hold')
 await page.evaluate(()=>{const f=(window as any).native;f.client.sendOperation({operation:'insert',position:1,value:'B',path:['text']});f.client.connect();f.client.connect()})
 await expect.poll(async()=>(await state(request)).Held).toBe(true)
 expect(await page.evaluate(()=>(window as any).native.client.isConnected())).toBe(false)
 expect((await state(request)).Ops).toHaveLength(1)
 await request.post(backend+'/__test/release');await expect.poll(()=>page.evaluate(()=>(window as any).native.client.getVersion())).toBe(2)
 const s=await state(request);expect(s.Wire).toHaveLength(2);expect(s.Ops.map((o:any)=>[o.Version,o.Data.value])).toEqual([[1,'A'],[2,'B']])
 await save('reconnect',page,request)
})
test('native auth and room rejection stop auto reconnect and never edit',async({page,request})=>{
 await page.clock.install();await create(page,'invalid-simulation-token')
 await expect.poll(()=>page.evaluate(()=>(window as any).native.errors.some((e:any)=>e.code==='AUTH_FAILED'))).toBe(true)
 await page.clock.runFor(6500);expect((await state(request)).Wire).toHaveLength(1)
 await page.evaluate(()=>(window as any).native.client.disconnect());await create(page,ready.Tokens.A,'missing-room')
 await expect.poll(()=>page.evaluate(()=>(window as any).native.errors.some((e:any)=>e.code==='FORBIDDEN'))).toBe(true)
 await page.clock.runFor(6500);const s=await state(request);expect(s.Wire).toHaveLength(2);expect(s.Ops).toEqual([]);expect(s.Wire.map((r:any)=>r.CloseCode)).toEqual([4001,4003])
 await save('denied',page,request)
})
test('native recoverable disconnect automatically opens a new signed connection',async({page,request})=>{
 await create(page);await expect.poll(()=>page.evaluate(()=>(window as any).native.client.isConnected())).toBe(true)
 await page.clock.install();await request.post(backend+'/__test/drop')
 await expect.poll(()=>page.evaluate(()=>(window as any).native.client.isConnected())).toBe(false)
 await page.clock.runFor(3500)
 await expect.poll(()=>page.evaluate(()=>(window as any).native.client.isConnected())).toBe(true)
 const s=await state(request);expect(s.Wire).toHaveLength(2);expect(s.Wire.every((r:any)=>r.User==='A')).toBe(true);expect(s.Ops).toEqual([])
 await save('auto-reconnect',page,request)
})
