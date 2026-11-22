import { test,expect } from './fixtures'
import type { Page } from '@playwright/test'
const old={id:'old',card_type:'qa',front:'原有问题是什么？',back:'原有答案',tags:[]}
const front='线粒体的主要作用是什么？',back='参与细胞呼吸，为细胞活动供能。'
function gate(){let release!:()=>void;const promise=new Promise<void>(r=>release=r);return{release,promise,started:false,ended:false}}
async function open(page:Page,cards=[old]){
 await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({json:[{id:'own',name:'生物复习',card_count:1}]}))
 await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({json:{cards}}))
 await page.goto('/flashcards');await expect(page.getByRole('button',{name:'手动添加',exact:true})).toBeEnabled({timeout:90000})
 await page.getByRole('button',{name:'手动添加',exact:true}).click()
 await expect(page.getByRole('dialog')).toBeVisible()
}
async function fill(page:Page){await page.getByLabel('问题 / 正面',{exact:true}).fill(front);await page.getByLabel('答案 / 解释',{exact:true}).fill(back)}
async function save(page:Page){await page.getByRole('button',{name:'保存卡片',exact:true}).click()}
test('native form submits actual API contract without generating and refreshes genuine due',async({page})=>{
 let sent:any;let generated=0,dueLoads=0
 page.on('request',r=>{if(r.url().includes('/flashcards/due?'))dueLoads++})
 await page.route('**/api/v1/flashcards/generate',r=>{generated++;return r.fulfill({json:{}})})
 await page.route('**/api/v1/flashcards/cards',r=>{sent=r.request().postDataJSON();return r.fulfill({status:201,json:{id:'new',...sent}})})
 await open(page);await fill(page);await save(page)
 await expect(page.getByRole('dialog')).toHaveCount(0)
 expect(sent).toEqual({deck_id:'own',card_type:'qa',front,back,hints:'',tags:[]});expect(generated).toBe(0)
 await expect.poll(()=>dueLoads).toBe(2)
 await expect(page.getByText('已添加到牌组',{exact:true})).toBeVisible()
})
test('native required and minimum validation prevent empty HTTP writes',async({page})=>{
 let writes=0;await page.route('**/api/v1/flashcards/cards',r=>{writes++;return r.fulfill({json:{}})})
 await open(page);await save(page);await expect(page.getByText('请输入问题或正面内容',{exact:true})).toBeVisible()
 await page.getByLabel('问题 / 正面',{exact:true}).fill('短');await page.getByLabel('答案 / 解释',{exact:true}).fill('答案');await save(page)
 await expect(page.getByText('正面至少需要4个字符',{exact:true})).toBeVisible();expect(writes).toBe(0)
})
test('failed save preserves all input and explicit retry writes once',async({page})=>{
 let writes=0;await page.route('**/api/v1/flashcards/cards',r=>++writes===1?r.fulfill({status:503,json:{detail:'temporary'}}):r.fulfill({status:201,json:{id:'new'}}))
 await open(page);await fill(page);await save(page)
 await expect(page.getByText('保存卡片失败',{exact:true})).toBeVisible();await expect(page.getByLabel('问题 / 正面',{exact:true})).toHaveValue(front)
 await page.getByRole('button',{name:'重新保存',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);expect(writes).toBe(2)
})
test('pending native form locks duplicate submits and page rating shortcuts',async({page})=>{
 const held=gate();let writes=0,ratings=0
 await page.route('**/api/v1/flashcards/cards',async r=>{writes++;held.started=true;await held.promise;await r.fulfill({status:201,json:{id:'new'}})})
 await page.route('**/api/v1/flashcards/review',r=>{ratings++;return r.fulfill({json:{}})})
 await open(page);await fill(page);await save(page);await expect.poll(()=>held.started).toBe(true)
  await expect(page.getByRole('dialog').getByRole('button',{name:/保存卡片/})).toBeDisabled()
  await page.evaluate(()=>{for(const key of [' ','3'])document.body.dispatchEvent(new KeyboardEvent('keydown',{key,bubbles:true}))})
 held.release();await expect(page.getByRole('dialog')).toHaveCount(0);expect(writes).toBe(1);expect(ratings).toBe(0)
})
test('cancel pending create ignores late success and has a fresh form on reopen',async({page})=>{
 const held=gate();await page.route('**/api/v1/flashcards/cards',async r=>{held.started=true;await held.promise;await r.fulfill({status:201,json:{id:'new'}}).catch(()=>{});held.ended=true})
 await open(page);await fill(page);await save(page);await expect.poll(()=>held.started).toBe(true)
 await page.getByRole('dialog').getByRole('button',{name:/取\s*消/}).click();held.release();await expect.poll(()=>held.ended).toBe(true)
 await page.getByRole('button',{name:'手动添加',exact:true}).click();await expect(page.getByLabel('问题 / 正面',{exact:true})).toHaveValue('')
 await expect(page.getByText('已添加到牌组',{exact:true})).toHaveCount(0)
})
test('same-account token refresh retains native form content',async({page})=>{
 await open(page);await fill(page)
 await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');const s=useAuthStore.getState();s.login('renewed',s.user!)})
 await expect(page.getByLabel('问题 / 正面',{exact:true})).toHaveValue(front)
})
test('account replacement drops private form and late success',async({page})=>{
 const held=gate();await page.route('**/api/v1/flashcards/cards',async r=>{held.started=true;await held.promise;await r.fulfill({status:201,json:{id:'new'}}).catch(()=>{});held.ended=true})
 await open(page);await fill(page);await save(page);await expect.poll(()=>held.started).toBe(true)
 await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('b',{id:'b',username:'b',email:'b@example.test',created_at:''})})
 held.release();await expect.poll(()=>held.ended).toBe(true);await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByText('已添加到牌组',{exact:true})).toHaveCount(0)
})
test('deck navigation drops old private form',async({page})=>{
 await open(page);await fill(page)
 await page.evaluate(()=>{history.pushState(null,'','/flashcards?deck_id=B');dispatchEvent(new PopStateEvent('popstate'))})
 await expect(page.getByRole('dialog')).toHaveCount(0)
})
test('successful create with failed due reload is not reported as failed save',async({page})=>{
 await page.route('**/api/v1/flashcards/cards',r=>r.fulfill({status:201,json:{id:'new'}}))
 await open(page);await fill(page)
 await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({status:503,json:{detail:'temporary'}}));await save(page)
 await expect(page.getByText('待复习卡片加载失败',{exact:true})).toBeVisible();await expect(page.getByText('保存卡片失败',{exact:true})).toHaveCount(0)
})
test('cloze native select sends existing card type and backend validation remains recoverable',async({page})=>{
 let sent:any;await page.route('**/api/v1/flashcards/cards',r=>{sent=r.request().postDataJSON();return r.fulfill({status:422,json:{detail:'Invalid card content'}})})
 await open(page);await fill(page);await page.getByRole('dialog').getByText('问答卡',{exact:true}).click();await page.getByText('填空卡',{exact:true}).click();await save(page)
 await expect(page.getByText('卡片内容无效',{exact:true})).toBeVisible();expect(sent.card_type).toBe('cloze');await expect(page.getByLabel('问题 / 正面',{exact:true})).toHaveValue(front)
})
