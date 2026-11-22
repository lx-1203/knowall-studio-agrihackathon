import { test,expect,stubBackend } from './fixtures'
import type { Page } from '@playwright/test'
const lists='**/api/v1/study/plans',detail='**/api/v1/study/plans/*'
const plan=(id='A')=>({id,name:'计划'+id,description:'描述'+id,status:'active',progress:0,goals:[{id:'goal'+id,title:'目标'+id,priority:'medium',completed:false}]})
function gate(){let release!:()=>void;return {promise:new Promise<void>(r=>release=r),release:()=>release()}}
async function switchAccount(page:Page){await page.evaluate(async()=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('account-b',{id:'B',username:'B',email:'b@example.invalid'} as any)})}
async function newPlan(page:Page,name:string){await page.getByRole('button',{name:/New Plan/}).click();await page.getByPlaceholder('Plan name').fill(name)}
async function submit(page:Page){await page.getByRole('dialog').getByRole('button',{name:/^(?:OK|确\s*定)$/}).click()}
test.beforeEach(async({page})=>{await stubBackend(page);await page.route(lists,r=>r.fulfill({json:[plan(r.request().headers()['authorization']?.includes('account-b')?'B':'A')]}));await page.route(detail,r=>r.fulfill({json:plan(new URL(r.request().url()).pathname.split('/').at(-1))}))})
test('account switch clears loaded plans details and creation drafts',async({page})=>{
 await page.goto('/study');await page.getByRole('row').filter({hasText:'计划A'}).click();await expect(page.getByText('目标A',{exact:true})).toBeVisible();await newPlan(page,'旧账号草稿');await switchAccount(page)
 await expect(page.getByRole('row').filter({hasText:'计划B'})).toBeVisible();await expect(page.getByText('目标A',{exact:true})).toBeHidden();await expect(page.getByRole('dialog')).toBeHidden();await newPlan(page,'');await expect(page.getByPlaceholder('Plan name')).toHaveValue('')
})
test('old delayed list cannot replace the new account list',async({page})=>{
 const hold=gate();let calls=0;await page.route(lists,async r=>{calls++;const old=!r.request().headers()['authorization']?.includes('account-b');if(old)await hold.promise;await r.fulfill({json:[plan(old?'A':'B')]}).catch(()=>{})})
 await page.goto('/study');await expect.poll(()=>calls).toBe(1);await switchAccount(page)
 try{await expect(page.getByRole('row').filter({hasText:'计划B'})).toBeVisible()}finally{hold.release()}
 await expect(page.getByRole('row').filter({hasText:'计划A'})).toBeHidden()
})
test('old delayed detail cannot reveal goals after switching account',async({page})=>{
 const hold=gate();let arrived=false;await page.route('**/api/v1/study/plans/A',async r=>{arrived=true;await hold.promise;await r.fulfill({json:plan()}).catch(()=>{})})
 await page.goto('/study');await page.getByRole('row').filter({hasText:'计划A'}).click();await expect.poll(()=>arrived).toBe(true);await switchAccount(page);hold.release();await page.waitForTimeout(150)
 await expect(page.getByText('目标A',{exact:true})).toBeHidden();await expect(page.getByRole('row').filter({hasText:'计划B'})).toBeVisible()
})
for(const status of [200,503])test(`old create ${status} cannot close the new draft or report a result in the new account`,async({page})=>{
 const hold=gate();let calls=0,settled=false;await page.route(lists,async r=>{if(r.request().method()==='POST'){calls++;await hold.promise;await r.fulfill({status,json:{plan_id:'old',detail:'旧账号失败'}});settled=true}else await r.fulfill({json:[plan(r.request().headers()['authorization']?.includes('account-b')?'B':'A')]})})
 await page.goto('/study');await newPlan(page,'旧创建');await submit(page);await expect.poll(()=>calls).toBe(1);await switchAccount(page)
 await expect(page.getByRole('dialog')).toBeHidden()
 await newPlan(page,'新账号草稿');hold.release();await expect.poll(()=>settled).toBe(true);await page.waitForTimeout(150);await expect(page.getByPlaceholder('Plan name')).toHaveValue('新账号草稿');expect(await page.getByText(status===200?'Plan created':'Failed to create plan',{exact:true}).isVisible()).toBe(false)
})
test('new account creation is not blocked by the old in-flight write lock',async({page})=>{
 const hold=gate();let posts=0;await page.route(lists,async r=>{if(r.request().method()!=='POST')return r.fulfill({json:[]});posts++;if(posts===1)await hold.promise;await r.fulfill({json:{plan_id:'saved'}})})
 await page.goto('/study');await newPlan(page,'旧创建');await submit(page);await switchAccount(page)
 await expect(page.getByRole('dialog')).toBeHidden()
 await newPlan(page,'新创建');try{await submit(page);await expect.poll(()=>posts).toBe(2);await expect(page.getByRole('dialog')).toBeHidden()}finally{hold.release()}
})
test('a create response after leaving the page does not refresh or show a toast',async({page})=>{
 const hold=gate();let gets=0;await page.route(lists,async r=>{if(r.request().method()==='POST'){await hold.promise;return r.fulfill({json:{plan_id:'saved'}})}gets++;return r.fulfill({json:[]})})
 await page.goto('/quiz');await page.getByRole('link',{name:'学习计划',exact:true}).click();await newPlan(page,'离页创建');await submit(page);await page.goBack();await expect(page).toHaveURL(/\/quiz$/);const response=page.waitForResponse(r=>r.url().endsWith('/study/plans')&&r.request().method()==='POST');hold.release();await response;await page.waitForTimeout(150)
 expect(gets).toBe(1);expect(await page.getByText('Plan created',{exact:true}).isVisible()).toBe(false)
})
test('failed list refresh preserves saved plans and offers a retry',async({page})=>{
 let gets=0;await page.route(lists,r=>++gets===2?r.fulfill({status:503,json:{detail:'模拟中断'}}):r.fulfill({json:[plan()]}));await page.goto('/study');await expect(page.getByRole('row').filter({hasText:'计划A'})).toBeVisible()
 await page.getByRole('button',{name:/刷新计划$/}).click();await expect(page.getByRole('alert').filter({hasText:'加载计划失败'})).toBeVisible();await expect(page.getByRole('row').filter({hasText:'计划A'})).toBeVisible();await page.getByRole('button',{name:/刷新计划$/}).click();await expect(page.getByRole('alert').filter({hasText:'加载计划失败'})).toBeHidden();expect(gets).toBe(3)
})
test('stalled list becomes recoverable without presenting a false empty list',async({page})=>{
 const hold=gate();let gets=0;await page.clock.install();await page.route(lists,async r=>{if(++gets===1)await hold.promise;await r.fulfill({json:[plan()]}).catch(()=>{})});await page.goto('/study');await expect.poll(()=>gets).toBe(1);await page.clock.runFor(15001)
 try{await expect(page.getByRole('alert').filter({hasText:'加载计划失败'})).toBeVisible();await expect(page.getByText('No study plans yet',{exact:true})).toBeHidden();await page.getByRole('button',{name:/刷新计划$/}).click();await expect(page.getByRole('row').filter({hasText:'计划A'})).toBeVisible()}finally{hold.release()}
})
test('detail error can be retried for the selected plan',async({page})=>{
 let calls=0;await page.route('**/api/v1/study/plans/A',r=>++calls===1?r.fulfill({status:503,json:{detail:'模拟中断'}}):r.fulfill({json:plan()}));await page.goto('/study');await page.getByRole('row').filter({hasText:'计划A'}).click();await expect(page.getByRole('alert').filter({hasText:'加载计划详情失败'})).toBeVisible();await page.getByRole('button',{name:'重试详情',exact:true}).click();await expect(page.getByText('目标A',{exact:true})).toBeVisible();expect(calls).toBe(2)
})
test('old goal toggle completion does not trigger a new-account list read',async({page})=>{
 const hold=gate();let bGets=0;await page.route(lists,r=>{const b=r.request().headers()['authorization']?.includes('account-b');if(b)bGets++;return r.fulfill({json:[plan(b?'B':'A')]})});await page.route('**/api/v1/study/goals/goalA/toggle',async r=>{await hold.promise;await r.fulfill({json:{completed:true}})})
 await page.goto('/study');await page.getByRole('row').filter({hasText:'计划A'}).click();await page.getByRole('checkbox').click();await switchAccount(page);try{await expect.poll(()=>bGets).toBe(1)}finally{hold.release()};await page.waitForTimeout(150);expect(bGets).toBe(1)
})
test('old simulated AI generation does not announce success in the new account',async({page})=>{
 const hold=gate();let arrived=false;await page.route('**/api/v1/study/generate-plan',async r=>{arrived=true;await hold.promise;await r.fulfill({json:{name:'旧模拟AI计划',goal_count:1}})})
 await page.goto('/study');await page.evaluate(async()=>{const {useAppStore}=await import('/src/stores/index.ts');useAppStore.setState({selectedDoc:'docA'})});await page.getByRole('button',{name:/AI 生成计划/}).click();await expect.poll(()=>arrived).toBe(true);await switchAccount(page);const response=page.waitForResponse('**/api/v1/study/generate-plan');hold.release();await response;await page.waitForTimeout(150);expect(await page.getByText(/AI已生成学习计划/).isVisible()).toBe(false)
})
for(const action of ['delete-plan','add-goal','delete-goal'])test(`late ${action} cannot refresh or mutate the new account view`,async({page})=>{
 const hold=gate();let bGets=0,arrived=false,settled=false
 await page.route(lists,r=>{const b=r.request().headers()['authorization']?.includes('account-b');if(b)bGets++;return r.fulfill({json:[plan(b?'B':'A')]})})
 const endpoint=action==='delete-plan'?'**/api/v1/study/plans/A':action==='add-goal'?'**/api/v1/study/goals':'**/api/v1/study/goals/goalA'
 await page.route(endpoint,async r=>{if(r.request().method()==='GET')return r.fulfill({json:plan()});arrived=true;await hold.promise;await r.fulfill({json:{status:'deleted',goal_id:'new'}});settled=true})
 await page.goto('/study');const row=page.getByRole('row').filter({hasText:'计划A'});await row.click();await expect(page.getByText('目标A',{exact:true})).toBeVisible()
 if(action==='delete-plan'){await row.getByRole('button').click();await page.locator('.ant-popconfirm').getByRole('button',{name:/^(?:OK|确\s*定)$/}).click()}
 else if(action==='add-goal'){page.once('dialog',d=>d.accept('新增目标'));await page.getByRole('button',{name:/Add Goal/}).click()}
 else await page.locator('.ant-list-item').filter({hasText:'目标A'}).getByRole('button').click()
 await expect.poll(()=>arrived).toBe(true);await switchAccount(page)
 try{await expect.poll(()=>bGets).toBe(1)}finally{hold.release()}
 await expect.poll(()=>settled).toBe(true);expect(bGets).toBe(1);expect(await page.getByText('Plan deleted',{exact:true}).isVisible()).toBe(false);await expect(page.getByText('目标A',{exact:true})).toBeHidden()
})
test('a newer list refresh wins over an older held snapshot',async({page})=>{
 const hold=gate();let gets=0,settled=false
 await page.route(lists,async r=>{const index=++gets;if(index===2)await hold.promise;await r.fulfill({json:[plan(index===2?'old':index===3?'fresh':'A')]}).catch(()=>{});if(index===2)settled=true})
 await page.goto('/study');await expect(page.getByRole('row').filter({hasText:'计划A'})).toBeVisible();await page.getByRole('button',{name:/刷新计划$/}).click();await expect.poll(()=>gets).toBe(2)
 // Re-open the same route through a goal operation, giving a newer successful list request.
 await page.getByRole('button',{name:/New Plan/}).click();await page.getByPlaceholder('Plan name').fill('刷新来源')
 await page.route(lists,async r=>r.request().method()==='POST'?r.fulfill({json:{plan_id:'fresh'}}):r.fallback())
 await submit(page);await expect(page.getByRole('row').filter({hasText:'计划fresh'})).toBeVisible();hold.release();await expect.poll(()=>settled).toBe(true);await expect(page.getByRole('row').filter({hasText:'计划old'})).toBeHidden()
})
test('stalled detail has an explicit retry and does not leave old goal actions',async({page})=>{
 const hold=gate();let calls=0;await page.clock.install();await page.route('**/api/v1/study/plans/A',async r=>{if(++calls===1)await hold.promise;await r.fulfill({json:plan()}).catch(()=>{})})
 await page.goto('/study');await page.getByRole('row').filter({hasText:'计划A'}).click();await expect.poll(()=>calls).toBe(1);await page.clock.runFor(15001)
 try{await expect(page.getByRole('alert').filter({hasText:'加载计划详情失败'})).toBeVisible();await expect(page.getByText('目标A',{exact:true})).toBeHidden();await page.getByRole('button',{name:'重试详情',exact:true}).click();await expect(page.getByText('目标A',{exact:true})).toBeVisible()}finally{hold.release()}
})
test('successful creation clears the visible deadline for the next draft',async({page})=>{
 let saved:any;await page.route(lists,async r=>{if(r.request().method()==='POST'){saved=r.request().postDataJSON();return r.fulfill({json:{plan_id:'saved'}})}return r.fulfill({json:[]})})
 await page.goto('/study');await newPlan(page,'带截止日');const date=page.getByPlaceholder('Target end date');await date.fill('2026-10-20');await date.press('Enter');await submit(page)
 await expect(page.getByRole('dialog')).toBeHidden();expect(await page.evaluate(v=>new Date(v).getDate(),saved.target_end_date)).toBe(20);await newPlan(page,'下个草稿');await expect(date).toHaveValue('')
})
