import { test, expect } from './fixtures'
const deck={id:'recover',name:'导出恢复',card_count:1}
const exportPath='**/api/v1/flashcards/export/anki/*'
test.beforeEach(async({page})=>{
 await page.setViewportSize({width:1500,height:1000})
 await page.route('**/api/v1/flashcards/decks?*',r=>r.fulfill({json:[deck]}))
 await page.route('**/api/v1/flashcards/due?*',r=>r.fulfill({json:{cards:[{id:'one',card_type:'qa',front:'恢复题面',back:'答案'}]}}))
})
test('native503 retry reports progress then downloads once',async({page})=>{
 let calls=0;const files:string[]=[];page.on('download',d=>files.push(d.suggestedFilename()))
 await page.route(exportPath,r=>++calls===1?r.fulfill({status:503,headers:{'Retry-After':'1'},json:{detail:'private diagnostic'}}):r.fulfill({contentType:'application/zip',body:Buffer.from('PK-recovered')}))
 await page.goto('/flashcards',{waitUntil:'domcontentloaded'})
 const download=page.waitForEvent('download');await page.getByRole('row').filter({hasText:deck.name}).getByRole('button',{name:/导出Anki$/}).click()
 await expect(page.getByRole('status').filter({hasText:'正在重试'})).toContainText('1/2')
 await download;expect(calls).toBe(2);expect(files).toHaveLength(1)
 await expect(page.getByRole('button',{name:'取消导出',exact:true})).toHaveCount(0)
 await expect(page.getByText('private diagnostic')).toHaveCount(0)
})
test('cancel native Retry-After wait leaves card review alive and permits a fresh export',async({page})=>{
 let calls=0;let recovery=false;const files:string[]=[];page.on('download',d=>files.push(d.suggestedFilename()))
 await page.route(exportPath,r=>{calls++;return r.fulfill(recovery?{contentType:'application/zip',body:Buffer.from('PK-fresh')}:{status:503,headers:{'Retry-After':'3'},json:{detail:'private lock'}})})
 await page.goto('/flashcards',{waitUntil:'domcontentloaded'})
 const button=page.getByRole('row').filter({hasText:deck.name}).getByRole('button',{name:/导出Anki$/});await button.click()
 await expect(page.getByRole('status').filter({hasText:'正在重试'})).toBeVisible()
 await page.getByRole('button',{name:'取消导出',exact:true}).click();await expect(button).toBeEnabled()
 await expect(page.getByText('Anki 导出失败',{exact:true})).toHaveCount(0)
 expect(calls).toBe(1);expect(files).toHaveLength(0)
 await page.locator('.flashcard-flip').click();await expect(page.getByRole('button',{name:/正常回忆/})).toBeVisible()
 recovery=true;const download=page.waitForEvent('download');await button.click();await download
 expect(calls).toBe(2);expect(files).toHaveLength(1)
})
test('pending request can be cancelled before any response',async({page})=>{
 let release!:()=>void;const held=new Promise<void>(r=>release=r);let begun=false
 await page.route(exportPath,async r=>{begun=true;await held;await r.fulfill({contentType:'application/zip',body:Buffer.from('PK-late')}).catch(()=>{})})
 const files:string[]=[];page.on('download',d=>files.push(d.suggestedFilename()))
 await page.goto('/flashcards',{waitUntil:'domcontentloaded'});const button=page.getByRole('row').filter({hasText:deck.name}).getByRole('button',{name:/导出Anki$/});await button.click()
 await expect.poll(()=>begun).toBe(true);await page.getByRole('button',{name:'取消导出',exact:true}).click()
 await expect(button).toBeEnabled();release();await expect(page.getByText('Anki 导出失败',{exact:true})).toHaveCount(0)
 expect(files).toHaveLength(0)
})

test('account replacement during retry removes old progress and stops private export',async({page})=>{
 let calls=0;const files:string[]=[];page.on('download',d=>files.push(d.suggestedFilename()))
 await page.route(exportPath,r=>{calls++;return r.fulfill({status:503,headers:{'Retry-After':'3'},json:{detail:'private'}})})
 await page.goto('/flashcards',{waitUntil:'domcontentloaded'});await page.getByRole('row').filter({hasText:deck.name}).getByRole('button',{name:/导出Anki$/}).click()
 await expect(page.getByRole('status').filter({hasText:'正在重试'})).toBeVisible()
 await page.evaluate(async()=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('B-token',{id:'B',username:'B',email:'B@example.test'})})
 await expect(page.locator('.product-account')).toHaveText('B');await expect(page.getByRole('status').filter({hasText:'正在重试'})).toHaveCount(0)
 await expect(page.getByRole('button',{name:'取消导出',exact:true})).toHaveCount(0)
 expect(calls).toBe(1);expect(files).toHaveLength(0)
})

test('native second retry is shown and exhaustion retains explicit recovery',async({page})=>{
 let calls=0;await page.route(exportPath,r=>{calls++;return r.fulfill({status:503,json:{detail:'private'}})})
 await page.goto('/flashcards',{waitUntil:'domcontentloaded'});await page.getByRole('row').filter({hasText:deck.name}).getByRole('button',{name:/导出Anki$/}).click()
 await expect(page.getByRole('status').filter({hasText:'正在重试'})).toContainText('2/2')
 await expect(page.getByText('Anki 导出失败',{exact:true})).toBeVisible();expect(calls).toBe(3)
 await expect(page.getByRole('button',{name:'重新导出',exact:true})).toBeEnabled()
 await expect(page.getByRole('button',{name:'取消导出',exact:true})).toHaveCount(0)
})
