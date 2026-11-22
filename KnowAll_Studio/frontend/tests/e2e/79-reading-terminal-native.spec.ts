import { test, expect } from './fixtures'
import { readFileSync,writeFileSync,mkdirSync } from 'node:fs'
import path from 'node:path'
const file=process.env.READING_TERMINAL_NATIVE_READY
const ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'Needs isolated actual reading/SQLite server, remote AI forbidden')
for (const scenario of ['dictionary','constructor','__proto__']) {
 test(`actual SQLite reading ${scenario} and second cached read`,async({page,request})=>{
  const start=new Date().toISOString();const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
  const setup=await (await request.post(ready.BackendURL+'/__test/reset',{data:{Scenario:scenario}})).json()
  await page.route('**/api/v1/reading/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname}))
  await page.goto('/reading');const item=page.locator('.reading-article-item').filter({hasText:setup.Title});await item.click()
  await expect(page.locator('.reading-text')).toContainText(scenario==='dictionary'?setup.Text:'原生 '+scenario)
  const saved=async()=>JSON.parse(await page.evaluate(()=>localStorage.getItem('reading_lang_vocab')||'{}'))
  await expect.poll(async()=>(await saved()).totalRead).toBe(1)
  await item.click();await expect.poll(async()=>(await saved()).totalRead).toBe(2)
  const final=await (await request.get(ready.BackendURL+'/__test/state')).json();const storage=await saved()
  const conversions=final.Samples.filter((s:any)=>s.Path.endsWith('/convert/stream'))
  expect(conversions).toHaveLength(2);expect(final.Samples.filter((s:any)=>s.Path.endsWith('/convert'))).toEqual([])
  expect(conversions.map((s:any)=>s.Status)).toEqual([200,200]);expect(JSON.parse(conversions[1].Chunks.join('').slice(6)).cached).toBe(true)
  if(scenario==='dictionary'){expect(storage.words).toEqual({});expect(final.Cache).toHaveLength(1)}
  else {expect(Object.prototype.hasOwnProperty.call(storage.words,scenario)).toBe(true);expect(storage.words[scenario]).toMatchObject({cn:'自有词项',seen:2});expect(JSON.parse(conversions[1].Body).seen_words).toEqual([{cn:'自有词项',en:scenario}]);expect(final.Cache).toHaveLength(2)}
  expect(errors).toEqual([]);expect(final.ExternalAI).toBe(0)
  await page.screenshot({path:test.info().outputPath('terminal-native.png')})
  const folder=process.env.READING_TERMINAL_NATIVE_EVIDENCE!;mkdirSync(folder,{recursive:true});writeFileSync(path.join(folder,scenario+'.json'),JSON.stringify({Start:start,End:new Date().toISOString(),Ready:ready,Final:final,Storage:storage,PageErrors:errors},null,2))
 })
}
