import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'
const article = { id: 1, title: '终态边界', content: '我们的阅读内容需要保留合法英文词。', difficulty: 1, tags: [] }
// Return JSON text: Playwright's object-value transport drops an own __proto__ key.
const saved = async (page: Page) => JSON.parse(await page.evaluate(() => localStorage.getItem('reading_lang_vocab') || '{}'))
const pick = (page: Page) => page.locator('.reading-article-item').filter({ hasText: article.title }).click()
const stream = (payload: unknown) => 'data: '+JSON.stringify(payload)+'\n\n'
test.beforeEach(async ({ page }) => {
 await page.route('**/api/v1/reading/articles*',r=>r.fulfill({json:[article]}))
 await page.route('**/api/v1/reading/articles/1',r=>r.fulfill({json:article}))
})
for (const cached of [false,true]) {
 test(`unknown constructor stays plain text in ${cached?'cached':'streamed'} result`,async ({page})=>{
  let fallbacks=0
  await page.route('**/api/v1/reading/convert',r=>{fallbacks++;return r.fulfill({status:500,json:{detail:'unexpected fallback'}})})
  await page.route('**/api/v1/reading/convert/stream',r=>r.fulfill({contentType:'text/event-stream',body:stream({[cached?'cached':'done']:true,result:'合法 constructor 与 energy',vocabulary:[{en:'energy',cn:'能量'}]})}))
  await page.goto('/reading');await pick(page)
  await expect(page.locator('.reading-text')).toContainText('合法 constructor')
  expect(fallbacks).toBe(0);expect(await saved(page)).toMatchObject({totalRead:1,totalEncountered:1,words:{energy:{seen:1,cn:'能量'}}})
  expect(Object.prototype.hasOwnProperty.call((await saved(page)).words,'constructor')).toBe(false)
 })
}
// Keys that collide with Object.prototype must remain real own vocabulary entries.
for (const word of ['constructor','__proto__']) {
 test(`word key ${word} persists as own data and next request remains valid`,async ({page})=>{
  const requests:any[]=[]
  await page.route('**/api/v1/reading/convert/stream',r=>{requests.push(r.request().postDataJSON());return r.fulfill({contentType:'text/event-stream',body:stream({done:true,result:'合法 '+word,vocabulary:[{en:word,cn:'自有词项'}]})})})
  await page.goto('/reading');await pick(page)
  await expect.poll(async()=> (await saved(page)).totalRead).toBe(1)
  await test.info().attach('word-storage-boundary', { body: JSON.stringify({
   raw: await page.evaluate(()=>localStorage.getItem('reading_lang_vocab')),
   browserOwn: await page.evaluate(word=>Object.prototype.hasOwnProperty.call(JSON.parse(localStorage.getItem('reading_lang_vocab')||'{}').words,word),word),
   parsedOwn: Object.prototype.hasOwnProperty.call((await saved(page)).words,word),
  }), contentType:'application/json' })
  const words=(await saved(page)).words;expect(Object.prototype.hasOwnProperty.call(words,word)).toBe(true);expect(words[word]).toMatchObject({seen:1,cn:'自有词项'})
  await page.getByRole('button',{name:/换一批词/}).click();await expect.poll(()=>requests.length).toBe(2)
  expect(requests[1].seen_words).toEqual([{en:word,cn:'自有词项'}]);await expect.poll(async()=> (await saved(page)).totalRead).toBe(2)
  expect((await saved(page)).words[word].seen).toBe(2)
 })
}
const bads:unknown[]=[ [{en:'ghost'}],[{en:'ghost',cn:4}],[{en:'ghost',cn:' '}],[{en:' ',cn:'空白'}],[{en:4,cn:'数值'}],[null],['ghost'],{en:'ghost',cn:'词项'} ]
for (const [index,vocabulary] of bads.entries()) {
 test(`bad terminal vocabulary ${index} falls back before any storage write`,async ({page})=>{
  let fallbacks=0;let beforeFallback:any
  await page.route('**/api/v1/reading/convert/stream',r=>r.fulfill({contentType:'text/event-stream',body:stream({done:true,result:'坏终态未出现词项',vocabulary})}))
  await page.route('**/api/v1/reading/convert',async r=>{fallbacks++;beforeFallback=await saved(page);return r.fulfill({json:{result:'恢复 energy',vocabulary:[{en:'energy',cn:'能量'}]}})})
  await page.goto('/reading');await pick(page);await expect(page.locator('.reading-text')).toContainText('恢复 energy')
  expect(fallbacks).toBe(1);expect(beforeFallback).toEqual({});expect(await saved(page)).toMatchObject({totalRead:1,totalEncountered:1,words:{energy:{seen:1}}})
  expect(Object.keys((await saved(page)).words)).toEqual(['energy'])
 })
}
for (const vocabulary of [undefined,[]]) {
 test(`legal ${vocabulary===undefined?'omitted':'empty'} vocabulary completes once`,async({page})=>{
  await page.route('**/api/v1/reading/convert/stream',r=>r.fulfill({contentType:'text/event-stream',body:stream({done:true,result:'纯中文合法正文',vocabulary})}))
  await page.goto('/reading');await pick(page);await expect(page.locator('.reading-text')).toContainText('纯中文合法正文')
  expect(await saved(page)).toEqual({words:{},totalRead:1,totalEncountered:0})
 })
}
test('invalid fallback cannot poison words and next valid reading recovers',async({page})=>{
 let valid=false
 await page.route('**/api/v1/reading/convert/stream',r=>r.fulfill({status:503,json:{detail:'explicit interrupted stream'}}))
 await page.route('**/api/v1/reading/convert',r=>r.fulfill({json:valid?{result:'恢复 energy',vocabulary:[{en:'energy',cn:'能量'}]}:{result:'坏终态正文',vocabulary:[{en:'ghost',cn:4}]}}))
 await page.goto('/reading');await pick(page);await expect(page.getByText('请求失败，请检查服务是否运行')).toBeVisible()
 expect(await saved(page)).toEqual({});valid=true;await pick(page);await expect(page.locator('.reading-text')).toContainText('恢复 energy')
 expect((await saved(page)).totalRead).toBe(1)
})
