import { test,expect,stubBackend,E2E_TOKEN,E2E_USER } from './fixtures'
import type { Page } from '@playwright/test'
const due='**/api/v1/study/reminders/due',read='**/api/v1/study/reminders/*/read'
const original={id:'r',message:'原会话提醒',remind_at:'2026-10-09T00:00:00',repeat_daily:false}
async function login(page:Page,token:string,id='e2e-user'){
 await page.evaluate(async({token,id})=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login(token,{id,username:id,email:id+'@fixture.invalid'} as any)},{token,id})
}
test.beforeEach(async({page})=>{await stubBackend(page)})
for(const mode of ['aba','refresh','batched-aba','same-token-login'])test(`pending old dismissal does not block ${mode} current reminders`,async({page})=>{
 let phase='original',released=false,posts=0,release!:()=>void
 const gate=new Promise<void>(r=>release=r)
 await page.route(due,r=>r.fulfill({json:{reminders:[{...original,id:phase==='original'?'r':'new',message:phase==='original'?original.message:'当前会话提醒'}]}}))
 await page.route(read,async r=>{posts++;if(posts===1){await gate;released=true}await r.fulfill({json:{status:'read'}}).catch(()=>{})})
 await page.goto('/study');await expect(page.getByText(original.message,{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'Dismiss'}).click();await expect.poll(()=>posts).toBe(1);phase='new'
 if(mode==='aba'){await login(page,'account-b','B');await expect(page.getByText('当前会话提醒',{exact:true})).toBeVisible();await login(page,E2E_TOKEN)}
 else if(mode==='refresh')await login(page,'refreshed-a')
 else if(mode==='same-token-login')await login(page,E2E_TOKEN)
 else await page.evaluate(async({a,user})=>{const {useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('account-b',{id:'B'} as any);useAuthStore.getState().login(a,user as any)},{a:E2E_TOKEN,user:E2E_USER})
 try{await expect(page.getByText('当前会话提醒',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Dismiss'})).toBeEnabled();expect(released).toBe(false)
 await page.getByRole('button',{name:'Dismiss'}).click();await expect.poll(()=>posts).toBe(2)}finally{release()}
})
for(const status of [200,401,500])test(`old ${status} dismissal cannot disturb a returned A session`,async({page})=>{
 let phase=0,posts=0,release!:()=>void;const gate=new Promise<void>(r=>release=r)
 await page.route(due,r=>r.fulfill({json:{reminders:[{...original,id:phase?'new':'r',message:phase?'当前会话提醒':original.message}]}}))
 await page.route(read,async r=>{posts++;await gate;await r.fulfill({status,json:{status:'old-response'}}).catch(()=>{})})
 await page.goto('/study');await page.getByRole('button',{name:'Dismiss'}).click();await expect.poll(()=>posts).toBe(1)
 await login(page,'account-b','B');await expect(page.getByText(original.message,{exact:true})).toBeVisible();phase=1;await login(page,E2E_TOKEN)
 // Clear the old write before checking the late auth/notification consequences.
 const oldResponse=page.waitForResponse(r=>r.url().includes('/reminders/r/read'))
 release();const response=await oldResponse;expect(response.status()).toBe(status);await response.finished()
 await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))))
 await expect(page.getByText('当前会话提醒',{exact:true})).toBeVisible()
 await expect.poll(()=>page.evaluate(()=>localStorage.getItem('knowall_token'))).toBe(E2E_TOKEN)
 await expect(page.getByText('更新提醒失败',{exact:true})).toBeHidden()
})
test('a current session 401 still logs out',async({page})=>{
 await page.route(due,r=>r.fulfill({json:{reminders:[original]}}));await page.route(read,r=>r.fulfill({status:401,json:{detail:'current token rejected'}}))
 await page.goto('/study');await page.getByRole('button',{name:'Dismiss'}).click()
 await expect.poll(()=>page.evaluate(()=>localStorage.getItem('knowall_token'))).toBeNull()
})
