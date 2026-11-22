import {test,expect} from './fixtures'
import type {Page} from '@playwright/test'
function gate(){let release!:()=>void;const promise=new Promise<void>(r=>release=r);return{promise,release,started:false,ended:false}}
async function open(page:Page){
 await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({json:[]}));await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({json:{cards:[]}}))
 await page.goto('/flashcards');await expect(page.getByRole('button',{name:'创建我的第一个牌组',exact:true})).toBeEnabled({timeout:90000});await page.getByRole('button',{name:'创建我的第一个牌组',exact:true}).click()
 await expect(page.getByRole('dialog')).toBeVisible()
}
async function fill(page:Page){await page.getByLabel('牌组名称',{exact:true}).fill('手工生物');await page.getByLabel('说明（可选）',{exact:true}).fill('补充遗漏知识点')}
async function save(page:Page){await page.getByRole('dialog').getByRole('button',{name:'创建并添加卡片',exact:true}).click()}

test('zero-document first deck creates and immediately enters existing native card form',async({page})=>{
 let sent:any;let ai=0;await page.route('**/api/v1/flashcards/generate',r=>{ai++;return r.fulfill({json:{}})})
 await page.route('**/api/v1/flashcards/decks',r=>{sent=r.request().postDataJSON();return r.fulfill({status:201,json:{id:'own',name:sent.name,card_count:0}})})
 await open(page);await fill(page);await save(page)
 await expect(page.getByRole('dialog',{name:'手动添加卡片',exact:true})).toBeVisible();await expect(page.getByText('添加到：手工生物',{exact:true})).toBeVisible()
 expect(sent).toEqual({name:'手工生物',description:'补充遗漏知识点'});expect(ai).toBe(0)
})
test('native first name validation prevents empty writes',async({page})=>{
 let writes=0;await page.route('**/api/v1/flashcards/decks',r=>{writes++;return r.fulfill({json:{}})})
 await open(page);await save(page);await expect(page.getByText('请输入牌组名称',{exact:true})).toBeVisible();expect(writes).toBe(0)
})
test('failed first deck retains values and explicit native retry creates once',async({page})=>{
 let writes=0;await page.route('**/api/v1/flashcards/decks',r=>++writes===1?r.fulfill({status:503,json:{detail:'temporary'}}):r.fulfill({status:201,json:{id:'own',name:'手工生物',card_count:0}}))
 await open(page);await fill(page);await save(page);await expect(page.getByText('创建牌组失败',{exact:true})).toBeVisible();await expect(page.getByLabel('牌组名称',{exact:true})).toHaveValue('手工生物')
 await page.getByRole('button',{name:'重新创建',exact:true}).click();await expect(page.getByRole('dialog',{name:'手动添加卡片',exact:true})).toBeVisible();expect(writes).toBe(2)
})
test('pending creation locks submit; cancel discards late success and reopened form is empty',async({page})=>{
 const held=gate();let writes=0;await page.route('**/api/v1/flashcards/decks',async r=>{writes++;held.started=true;await held.promise;await r.fulfill({status:201,json:{id:'own',name:'手工生物'}}).catch(()=>{});held.ended=true})
 await open(page);await fill(page);await save(page);await expect.poll(()=>held.started).toBe(true)
 await expect(page.getByRole('dialog').getByRole('button',{name:/创建并添加卡片/})).toBeDisabled();await page.getByRole('dialog').getByRole('button',{name:/取\s*消/}).click();held.release();await expect.poll(()=>held.ended).toBe(true)
 await page.getByRole('button',{name:'创建我的第一个牌组',exact:true}).click();await expect(page.getByLabel('牌组名称',{exact:true})).toHaveValue('');expect(writes).toBe(1)
})
test('same-account token renewal retains first-deck form',async({page})=>{
 await open(page);await fill(page);await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');const s=useAuthStore.getState();s.login('renewed',s.user!)})
 await expect(page.getByLabel('牌组名称',{exact:true})).toHaveValue('手工生物')
})
test('account replacement while creating ignores late first-deck modal',async({page})=>{
 const held=gate();await page.route('**/api/v1/flashcards/decks',async r=>{held.started=true;await held.promise;await r.fulfill({status:201,json:{id:'own',name:'A-private'}}).catch(()=>{});held.ended=true})
 await open(page);await fill(page);await save(page);await expect.poll(()=>held.started).toBe(true)
 await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('B',{id:'B',username:'B',email:'B@example.test',created_at:''})})
 held.release();await expect.poll(()=>held.ended).toBe(true);await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByText('牌组已创建',{exact:true})).toHaveCount(0)
})
test('deck navigation discards first-deck input',async({page})=>{
 await open(page);await fill(page);await page.evaluate(()=>{history.pushState(null,'','/flashcards?deck_id=B');dispatchEvent(new PopStateEvent('popstate'))});await expect(page.getByRole('dialog')).toHaveCount(0)
})
test('accepted creation followed by failed list reload still opens saved-deck card form',async({page})=>{
 await page.route('**/api/v1/flashcards/decks',r=>r.fulfill({status:201,json:{id:'own',name:'手工生物',card_count:0}}));await open(page);await fill(page)
 await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({status:503,json:{detail:'temporary'}}));await save(page)
 await expect(page.getByRole('dialog',{name:'手动添加卡片',exact:true})).toBeVisible();await expect(page.getByText('牌组列表加载失败',{exact:true})).toBeVisible();await expect(page.getByText('创建牌组失败',{exact:true})).toHaveCount(0)
})
