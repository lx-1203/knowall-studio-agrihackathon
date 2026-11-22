import {test,expect} from './fixtures'
import type {Page} from '@playwright/test'
const card={id:'card-A',card_type:'qa',front:'细胞供能的结构是什么？',back:'错误答案细胞核',hints:'旧提示',deck_id:'deck-A',tags:['生物'],schedule:{state:'new',review_count:0}}
async function open(page:Page,search=false){
 await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({json:[{id:'deck-A',name:'生物',card_count:1}]}))
 await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({json:{cards:search?[]:[card]}}))
 await page.route('**/api/v1/flashcards/cards/card-A',r=>r.request().method()==='GET'?r.fulfill({json:card}):r.fulfill({json:{...card,...r.request().postDataJSON()}}))
 await page.goto('/flashcards');await expect(page.getByText(search?'暂无待复习卡片':card.front,{exact:true})).toBeVisible({timeout:90000})
 if(search){await page.route('**/api/v1/flashcards/search?*',r=>r.fulfill({json:{results:[card]}}));await page.getByPlaceholder('搜索卡片...').fill('细胞');await page.getByPlaceholder('搜索卡片...').press('Enter')}
 await page.getByRole('button',{name:search?'编辑卡片':'编辑当前卡片',exact:true}).click()
 await expect(page.getByRole('dialog',{name:'编辑卡片内容',exact:true})).toBeVisible()
 await expect(page.getByLabel('答案 / 解释',{exact:true})).toHaveValue(card.back)
}
test('current card edit entrance reads current content',async({page})=>{await open(page)})
async function save(page:Page){await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click()}
test('save changes visible answer and retains remaining cards and cursor',async({page})=>{
 await open(page);let sent:any
 await page.evaluate(async card=>{const{useFlashcardStore}=await import('/src/stores/index.ts');useFlashcardStore.setState({dueCards:[card,{...card,id:'other'}],currentIndex:0,isFlipped:true})},card)
 await page.route('**/api/v1/flashcards/cards/card-A',r=>{sent=r.request().postDataJSON();return r.fulfill({json:{...card,...sent}})})
 await page.getByLabel('答案 / 解释',{exact:true}).fill('正确答案：**线粒体**');await save(page)
 await expect(page.getByRole('dialog')).toHaveCount(0)
 expect(sent.original).toEqual({card_type:'qa',front:card.front,back:card.back,hints:card.hints})
 const state=await page.evaluate(async()=>{const{useFlashcardStore}=await import('/src/stores/index.ts');const s=useFlashcardStore.getState();return{cards:s.dueCards,index:s.currentIndex,flipped:s.isFlipped}})
 expect(state.cards).toHaveLength(2);expect(state.cards[0].back).toBe('正确答案：**线粒体**');expect(state.index).toBe(0);expect(state.flipped).toBe(false)
})
test('failed patch keeps draft and only explicit retry writes again',async({page})=>{
 await open(page);let writes=0
 await page.route('**/api/v1/flashcards/cards/card-A',r=>++writes===1?r.fulfill({status:503,json:{detail:'temporary'}}):r.fulfill({json:{...card,back:'正确答案线粒体'}}))
 await page.getByLabel('答案 / 解释',{exact:true}).fill('正确答案线粒体');await save(page)
 await expect(page.getByText('保存修改失败',{exact:true})).toBeVisible();await expect(page.getByLabel('答案 / 解释',{exact:true})).toHaveValue('正确答案线粒体');expect(writes).toBe(1)
 await page.getByRole('button',{name:'重新保存',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);expect(writes).toBe(2)
})
test('stale content conflict retains draft without blind overwrite',async({page})=>{
 await open(page);let writes=0
 await page.route('**/api/v1/flashcards/cards/card-A',r=>{writes++;return r.fulfill({status:409,json:{detail:'Content changed'}})})
 await page.getByLabel('答案 / 解释',{exact:true}).fill('我的纠错草稿');await save(page)
 await expect(page.getByText('卡片已在其他地方更新',{exact:true})).toBeVisible();await expect(page.getByLabel('答案 / 解释',{exact:true})).toHaveValue('我的纠错草稿');expect(writes).toBe(1)
 await expect(page.getByRole('button',{name:'重新保存',exact:true})).toHaveCount(0)
})
test('native validation prevents empty edit writes',async({page})=>{
 await open(page);let writes=0;await page.route('**/api/v1/flashcards/cards/card-A',r=>{writes++;return r.fulfill({json:card})})
 await page.getByLabel('答案 / 解释',{exact:true}).fill(' ');await save(page);await expect(page.getByText('请输入答案或解释',{exact:true})).toBeVisible();expect(writes).toBe(0)
})
test('same-account token renewal keeps editor and blocks rating shortcuts',async({page})=>{
 let ratings=0;await page.route('**/api/v1/flashcards/review',r=>{ratings++;return r.fulfill({json:{}})})
 await open(page);await page.getByLabel('答案 / 解释',{exact:true}).fill('保留中的草稿')
 await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');const s=useAuthStore.getState();s.login('renewed',s.user!)})
 await expect(page.getByLabel('答案 / 解释',{exact:true})).toHaveValue('保留中的草稿');await page.keyboard.press('3');expect(ratings).toBe(0)
})
test('pending patch locks submit and account replacement discards late response',async({page})=>{
 await open(page);let release!:()=>void;const held=new Promise<void>(r=>release=r);let started=false,ended=false,writes=0
 await page.route('**/api/v1/flashcards/cards/card-A',async r=>{writes++;started=true;await held;await r.fulfill({json:{...card,back:'A私有修改'}}).catch(()=>{});ended=true})
 await save(page);await expect.poll(()=>started).toBe(true);await expect(page.getByRole('dialog').getByRole('button',{name:/保存修改/})).toBeDisabled()
 await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('B',{id:'B',username:'B',email:'B@example.test',created_at:''})})
 release();await expect.poll(()=>ended).toBe(true);await expect(page.getByRole('dialog')).toHaveCount(0);expect(writes).toBe(1);await expect(page.getByText('卡片已更新',{exact:true})).toHaveCount(0)
})
test('search allows editing a card that is no longer due',async({page})=>{
 await open(page,true);await page.getByLabel('答案 / 解释',{exact:true}).fill('搜索纠错后的答案');await save(page)
 await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByText('搜索纠错后的答案',{exact:true})).toBeVisible();await expect(page.getByText('暂无待复习卡片',{exact:true})).toBeVisible()
})
test('cancel and deck navigation discard the private draft',async({page})=>{
 await open(page);await page.getByLabel('答案 / 解释',{exact:true}).fill('未保存的输入');await page.getByRole('dialog').getByRole('button',{name:/取\s*消/}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
 await page.getByRole('button',{name:'编辑当前卡片',exact:true}).click();await expect(page.getByLabel('答案 / 解释',{exact:true})).toHaveValue(card.back)
 await page.evaluate(()=>{history.pushState(null,'','/flashcards?deck_id=B');dispatchEvent(new PopStateEvent('popstate'))});await expect(page.getByRole('dialog')).toHaveCount(0)
})
