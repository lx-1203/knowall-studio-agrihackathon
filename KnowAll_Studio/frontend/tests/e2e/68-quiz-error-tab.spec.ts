import { test,expect } from './fixtures'
const endpoint='**/api/v1/quiz/errors'
const row=(name:string)=>({error_id:name,error_count:1,question:{id:name,question_text:name+'私有错题',answer:'参考',question_type:'fill_blank'}})
test('opening error tab fetches saved errors and refresh can recover a failed read',async({page})=>{
  let calls=0
  await page.route(endpoint,r=>{calls++;return calls===1?r.fulfill({status:403,json:{detail:'模拟读取失败'}}):r.fulfill({json:[row('A')]})})
  await page.goto('/quiz');await page.getByRole('tab',{name:/错题/}).click()
  await expect(page.getByText('加载错题失败',{exact:true})).toBeVisible();expect(calls).toBe(1)
  await page.getByRole('tabpanel',{name:/错题/}).getByRole('button',{name:/刷新$/}).click()
  await expect(page.getByText('A私有错题',{exact:true})).toBeVisible();expect(calls).toBe(2)
})
test('in-place account switch reloads private error list and late old read cannot replace it',async({page})=>{
  let release!:()=>void;const gate=new Promise<void>(r=>release=r);let oldStarted=false,newCalls=0
  await page.route(endpoint,async r=>{
    const b=r.request().headers().authorization?.includes('B-token')
    if(!b){oldStarted=true;await gate}else newCalls++
    await r.fulfill({json:[row(b?'B':'A')]}).catch(()=>{})
  })
  await page.goto('/quiz');await page.getByRole('tab',{name:/错题/}).click();await expect.poll(()=>oldStarted).toBe(true)
  await page.evaluate(async()=>{
    // @ts-expect-error Vite resolves this absolute module only in browser
    const {useAuthStore}=await import('/src/stores/index.ts')
    useAuthStore.getState().login('B-token',{id:'B',username:'B',email:'B@fixture.invalid'})
  })
  await expect(page.getByText('B私有错题',{exact:true})).toBeVisible();expect(newCalls).toBe(1)
  release();await page.getByRole('tabpanel',{name:/错题/}).getByRole('button',{name:/刷新$/}).click()
  await expect.poll(()=>newCalls).toBe(2);await expect(page.getByText('B私有错题',{exact:true})).toBeVisible();await expect(page.getByText('A私有错题',{exact:true})).toBeHidden()
})
