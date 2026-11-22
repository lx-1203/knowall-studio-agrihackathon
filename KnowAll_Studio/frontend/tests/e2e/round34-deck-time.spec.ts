import {test,expect} from './fixtures'
const zones=[['Asia/Shanghai','2026-10-10','2027-01-01','2026-03-08'],['UTC','2026-10-09','2026-12-31','2026-03-08'],['America/New_York','2026-10-09','2026-12-31','2026-03-08']]
for(const [zone,night,year,dst] of zones) test.describe(zone,()=>{
 test.use({timezoneId:zone})
 test('review success displays the same instant in client timezone',async({page})=>{
  await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({json:[]}))
  await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({json:{cards:[{id:'clock',card_type:'qa',front:'当地复习日期',back:'解释'}]}}))
  await page.route('**/api/v1/flashcards/review',r=>r.fulfill({json:{next_review_at:'2026-10-09T19:40:00.123456',review_count:1}}))
  await page.goto('/flashcards',{waitUntil:'domcontentloaded'})
  await page.locator('.flashcard-flip').click()
  await page.getByRole('button',{name:'正常回忆 (3)',exact:true}).click()
  await expect(page.getByText(`正常回忆 — 下次复习: ${night}`,{exact:true})).toBeVisible()
  await expect(page.getByText('本轮复习完成',{exact:true})).toBeVisible()
 })
 test('real table interprets old UTC and offset-aware instants locally',async({page})=>{
  const cases=[['旧UTC','2026-10-09T19:40:00.123456',night],['显式UTC','2026-10-09T19:40:00Z',night],['正偏移','2026-10-10T03:40:00+08:00',night],['负偏移','2026-10-09T15:40:00-04:00',night],['跨年','2026-12-31T23:59:59Z',year],['夏令时','2026-03-08T07:00:00Z',dst],['纽约前夜','2026-03-08T04:00:00Z',zone==='America/New_York'?'2026-03-07':'2026-03-08'],['UTC元旦','2027-01-01T00:30:00Z',zone==='America/New_York'?'2026-12-31':'2027-01-01'],['坏数据','nonsense','—'],['缺时间',null,'—']]
  await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({json:cases.map(([name,created_at],i)=>({id:String(i),name,created_at,card_count:0}))}))
  await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({json:{cards:[]}}))
  await page.goto('/flashcards',{waitUntil:'domcontentloaded'})
  expect(await page.evaluate(()=>Intl.DateTimeFormat().resolvedOptions().timeZone)).toBe(zone)
  for(const [name,,date] of cases) await expect(page.getByRole('row').filter({hasText:String(name)}).getByRole('cell').nth(2)).toHaveText(String(date))
 })
})
