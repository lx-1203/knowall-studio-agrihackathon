import { test,expect,stubBackend } from './fixtures'
import type { Page } from '@playwright/test'
const due='**/api/v1/study/reminders/due', read='**/api/v1/study/reminders/r/read'
const item={id:'r',message:'驻留学习提醒',remind_at:'2026-10-09T00:00:00',repeat_daily:true}
async function focus(page:Page){await page.evaluate(()=>window.dispatchEvent(new Event('focus')))}
async function ready(page:Page){await page.goto('/study');await expect(page.getByText('No pending reminders')).toBeVisible()}
test.beforeEach(async({page})=>{await stubBackend(page)})
test('visible residency loads a newly due reminder without navigation',async({page})=>{
 let calls=0;await page.clock.install();await page.route(due,r=>r.fulfill({json:{reminders:++calls===1?[]:[item]}}));await ready(page)
 await page.clock.runFor(30001);await expect(page.getByText(item.message,{exact:true})).toBeVisible();expect(calls).toBe(2)
})
test('focus immediately discovers a due reminder',async({page})=>{
 let calls=0;await page.route(due,r=>r.fulfill({json:{reminders:++calls===1?[]:[item]}}));await ready(page);await focus(page);await expect(page.getByText(item.message,{exact:true})).toBeVisible()
})
test('a stalled reminder read becomes a visible error and can be retried',async({page})=>{
 let calls=0,release!:()=>void;const gate=new Promise<void>(r=>release=r)
 await page.clock.install();await page.route(due,async r=>{if(++calls===1){await gate;await r.fulfill({json:{reminders:[]}}).catch(()=>{})}else await r.fulfill({json:{reminders:[item]}})})
 await page.goto('/study');await expect.poll(()=>calls).toBe(1);await page.clock.runFor(15001)
 try{await expect(page.getByRole('alert').filter({hasText:'加载提醒失败'})).toBeVisible();await page.getByRole('button',{name:'刷新提醒',exact:true}).click();await expect(page.getByText(item.message,{exact:true})).toBeVisible()}finally{release()}
})
test('failed reading can be manually refreshed without hiding saved reminders',async({page})=>{
 let calls=0;await page.route(due,r=>++calls===2?r.fulfill({status:503,json:{detail:'模拟断网'}}):r.fulfill({json:{reminders:[item]}}))
 await page.goto('/study');await expect(page.getByText(item.message,{exact:true})).toBeVisible();await focus(page);await expect(page.getByRole('alert').filter({hasText:'加载提醒失败'})).toBeVisible();await expect(page.getByText(item.message,{exact:true})).toBeVisible()
 await page.getByRole('button',{name:/刷新提醒$/}).click();await expect(page.getByRole('alert').filter({hasText:'加载提醒失败'})).toBeHidden();expect(calls).toBe(3)
})
test('dismiss invalidates older in-flight reading and suppresses polling during writing',async({page})=>{
 let calls=0,posts=0,releaseGet!:()=>void,releasePost!:()=>void;const getGate=new Promise<void>(r=>releaseGet=r),postGate=new Promise<void>(r=>releasePost=r)
 await page.clock.install();await page.route(due,async r=>{const call=++calls;const snapshot=call>=3?[]:[item];if(call===2)await getGate;await r.fulfill({json:{reminders:snapshot}}).catch(()=>{})})
 await page.route(read,async r=>{posts++;await postGate;await r.fulfill({json:{status:'read'}})})
 await page.goto('/study');await expect(page.getByText(item.message,{exact:true})).toBeVisible();await focus(page);await expect.poll(()=>calls).toBe(2)
 await page.getByRole('button',{name:'Dismiss'}).click();await expect.poll(()=>posts).toBe(1);await page.clock.runFor(30001);expect(calls).toBe(2)
 releasePost();await expect(page.getByText(item.message,{exact:true})).toBeHidden();releaseGet();await page.waitForTimeout(150);await expect(page.getByText(item.message,{exact:true})).toBeHidden();expect(posts).toBe(1)
})
test('a pending dismissal serializes reminder actions until persistence is refreshed',async({page})=>{
 let posts=0,release!:()=>void;const gate=new Promise<void>(r=>release=r)
 await page.route(due,r=>r.fulfill({json:{reminders:[item,{...item,id:'s',message:'第二条提醒'}]}}))
 await page.route('**/api/v1/study/reminders/*/read',async r=>{posts++;await gate;await r.fulfill({json:{status:'read'}})})
 await page.goto('/study');await page.getByRole('button',{name:'Dismiss'}).first().click()
 try{await expect(page.getByRole('button',{name:'Dismiss'}).last()).toBeDisabled();expect(posts).toBe(1)}finally{release()}
 await expect(page.getByRole('button',{name:'Dismiss'}).last()).toBeEnabled()
})
test('an old account pending dismissal cannot block reminders for the new account',async({page})=>{
 let release!:()=>void;const gate=new Promise<void>(r=>release=r)
 await page.route(due,r=>r.fulfill({json:{reminders:[{...item,message:r.request().headers()['authorization']?.includes('e2e-token')?item.message:'新账号提醒'}]}}))
 await page.route(read,async r=>{await gate;await r.fulfill({json:{status:'read'}})})
 await page.goto('/study');await page.getByRole('button',{name:'Dismiss'}).click()
 await page.evaluate(async()=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('account-b',{id:'B',username:'B',email:'b@example.invalid'} as any)})
 try{await expect(page.getByText('新账号提醒',{exact:true})).toBeVisible()}finally{release()}
 await expect(page.getByText(item.message,{exact:true})).toBeHidden()
})
test('leaving the page stops the timer and focus listener',async({page})=>{
 let calls=0;await page.clock.install();await page.route(due,r=>{calls++;return r.fulfill({json:{reminders:[]}})});await ready(page);await page.goto('/quiz');const before=calls;await page.clock.runFor(60001);await focus(page);await page.waitForTimeout(100);expect(calls).toBe(before)
})
test('hidden tabs pause polling and becoming visible refreshes immediately',async({page})=>{
 let calls=0;await page.clock.install();await page.route(due,r=>r.fulfill({json:{reminders:++calls===1?[]:[item]}}));await ready(page)
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'))});await page.clock.runFor(60001);expect(calls).toBe(1)
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'))});await expect(page.getByText(item.message,{exact:true})).toBeVisible();expect(calls).toBe(2)
})
for(const status of [200,401])test(`old account late ${status} cannot display its reminder or log out the new account`,async({page})=>{
 let release!:()=>void,calls=0;const gate=new Promise<void>(r=>release=r)
 await page.route(due,async r=>{calls++;const auth=r.request().headers()['authorization'];if(auth?.includes('e2e-token')){await gate;await r.fulfill({status,json:status===200?{reminders:[item]}:{detail:'旧账号过期'}}).catch(()=>{})}else await r.fulfill({json:{reminders:[{...item,id:'b',message:'新账号提醒'}]}})})
 await page.goto('/study');await expect.poll(()=>calls).toBe(1)
 await page.evaluate(async()=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('account-b',{id:'B',username:'B',email:'b@example.invalid'} as any)})
 try{await expect(page.getByText('新账号提醒',{exact:true})).toBeVisible()}finally{release()}
 await page.waitForTimeout(150);await expect(page.getByText(item.message,{exact:true})).toBeHidden();expect(await page.evaluate(()=>localStorage.getItem('knowall_token'))).toBe('account-b')
})
