import { test,expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.STUDY_SCHEDULE_NATIVE_READY,ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Requires private real SQL/JWT localhost; no external AI')
for(const kind of ['both','short'])test(`native generated ${kind} schedule displays saved SQL after reload`,async({page,request})=>{
 const Start=new Date().toISOString(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await request.post(ready.BackendURL+'/__test/reset')
 const headers={Authorization:'Bearer '+ready.Tokens.A},bob={Authorization:'Bearer '+ready.Tokens.B},base=ready.BackendURL+'/api/v1/study'
 const generated=await request.post(base+'/generate-plan-enhanced',{headers,data:{summary_id:'summary',plan_type:kind,start_date:'2026-10-31',daily_hours:1.5,ebbinghaus_enabled:kind==='both'}});expect(generated.status()).toBe(200);const result=(await generated.json()).result
 const before=await (await request.get(ready.BackendURL+'/__test/state')).json();const saved=before.Rows.Plans.find((p:any)=>p.ID===result.plan_id);expect(saved.Short).toEqual(result.short_term_plan);expect(saved.Long).toEqual(result.long_term_plan);expect(saved.Review).toEqual(result.ebbinghaus_nodes)
 expect((await request.get(base+'/plans/'+result.plan_id,{headers:bob})).status()).toBe(404);expect((await request.get(base+'/plans/'+result.plan_id+'/ebbinghaus',{headers:bob})).status()).toBe(404)
 await stubBackend(page);await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'native-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
 await page.route('**/api/v1/study/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname}))
 await page.goto('/study')
 for(let read=0;read<2;read++){
  if(read)await page.reload()
  await page.getByRole('row').filter({hasText:result.name}).click();await page.getByText('短期排期',{exact:true}).click();const area=page.getByRole('region',{name:'短期排期'});await expect(area).toContainText(result.short_term_plan[0].date);await expect(area).toContainText(result.short_term_plan[0].hours[0].topic);await expect(area).toContainText('09:00-09:30')
  if(kind==='both'){await page.getByText('长期排期',{exact:true}).click();const long=page.getByRole('region',{name:'长期排期'});await expect(long).toContainText(result.long_term_plan[0].days[1].date);await expect(long).toContainText('1.5 小时');await page.getByText('复习节点',{exact:true}).click();await expect(page.getByRole('region',{name:'复习节点'})).toContainText(result.ebbinghaus_nodes[0].description)}else{await expect(page.getByText('长期排期',{exact:true})).toHaveCount(0);await expect(page.getByText('复习节点',{exact:true})).toHaveCount(0)}
 }
 await page.screenshot({path:path.join(path.dirname(file!),`native-${kind}.png`),fullPage:true})
 const Final=await (await request.get(ready.BackendURL+'/__test/state')).json();expect(Final.AIRequests).toEqual([]);expect(Final.SQLiteForeignKeys).toBe(1);expect(errors).toEqual([]);expect(Final.Rows.Plans).toHaveLength(3);expect(Final.BackendSamples.filter((s:any)=>s.Method==='POST'&&s.Path.includes('generate'))).toHaveLength(1)
 writeFileSync(path.join(path.dirname(file!),`real-browser-${kind}.json`),JSON.stringify({Start,End:new Date().toISOString(),ExternalAI:false,Generated:result,Before:before,Final,PageErrors:errors},null,2))
})
