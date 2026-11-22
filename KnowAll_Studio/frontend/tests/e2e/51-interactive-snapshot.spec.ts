import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const readyFile=process.env.INTERACTIVE_NATIVE_READY
const ready=readyFile?JSON.parse(readFileSync(readyFile,'utf8')):null
test.skip(!ready,'Requires private actual SQLite/JWT routes; SQL mutations explicitly injected, no AI')
for(const mode of ['changed-bank','deleted-bank']) {
  test(`interactive displayed snapshot survives ${mode} truthfully`,async({page,request})=>{
    const started=new Date().toISOString();const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
    await request.post(ready.BackendURL+'/__test/reset');await stubBackend(page)
    await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'snapshot-A',email:'A@fixture.invalid'}))},{token:ready.Tokens.A})
    await page.route('**/api/v1/quiz/interactive/**',r=>r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname+new URL(r.request().url()).search}))
    const state=async()=>(await request.get(ready.BackendURL+'/__test/state')).json()
    const begin=page.waitForResponse(r=>r.url().includes('/quiz/interactive/start'))
    await page.goto('/quiz/interactive/sum');const first=await(await begin).json()
    await expect(page.getByText('原生交互第1题',{exact:true})).toBeVisible()
    if(mode==='changed-bank') {
      await request.post(ready.BackendURL+'/__test/update',{data:{correct_answer:'B',question_text:'更新后的当前题',analysis:'新版本解析',options:[{label:'A',text:'新甲'},{label:'B',text:'新乙'}]}})
      const response=page.waitForResponse(r=>r.url().endsWith('/quiz/interactive/submit'))
      await page.getByRole('radio',{name:'A. 甲'}).check();const result=await response;expect(result.status()).toBe(200)
      const graded=await result.json();expect(graded.is_correct).toBeTruthy();expect(graded.correct_answer).toBe('A');expect(graded.analysis).toBe('甲为正确答案')
      await expect(page.getByText('甲为正确答案',{exact:true})).toBeVisible()
      await expect(page.getByText('原生交互第2题',{exact:true})).toBeVisible();await page.getByRole('radio',{name:'A. 甲'}).check()
      await expect(page.getByText('正确率: 100.0% (2/2)',{exact:true})).toBeVisible()
    } else {
      expect((await request.delete(ready.BackendURL+'/api/v1/quiz/bank/q1')).status()).toBe(200)
      const response=page.waitForResponse(r=>r.url().endsWith('/quiz/interactive/submit'))
      await page.getByRole('radio',{name:'A. 甲'}).check();expect((await response).status()).toBe(404)
      await expect(page.getByText('提交失败: Question not found',{exact:true})).toBeVisible();expect((await state()).AnswerRecords).toHaveLength(0)
      await expect(page.getByRole('radio',{name:'A. 甲'})).toBeEnabled();await expect(page.getByRole('radio',{name:'A. 甲'})).toBeChecked()
      await page.getByRole('button',{name:/答题卡/}).click();await page.getByRole('button',{name:'2',exact:true}).click()
      await page.getByRole('radio',{name:'A. 甲'}).check();await expect(page.getByText('答题完成！',{exact:true})).toBeVisible()
    }
    const headers={Authorization:'Bearer '+ready.Tokens.B}
    expect((await request.get(ready.BackendURL+`/api/v1/quiz/interactive/session/${first.session_id}/stats`,{headers})).status()).toBe(404)
    await page.reload()
    await expect(page.getByText(mode==='changed-bank'?'更新后的当前题':'原生交互第2题',{exact:true})).toBeVisible()
    await page.getByRole('radio',{name:mode==='changed-bank'?'B. 新乙':'A. 甲'}).check()
    if(mode==='changed-bank'){await expect(page.getByText('原生交互第2题',{exact:true})).toBeVisible();await page.getByRole('radio',{name:'A. 甲'}).check()}
    await expect(page.getByText(mode==='changed-bank'?'正确率: 100.0% (2/2)':'正确率: 100.0% (1/1)',{exact:true})).toBeVisible()
    const final=await state();expect(final.AnswerRecords).toHaveLength(mode==='changed-bank'?4:2);expect(final.AIRequests).toHaveLength(0);expect(errors).toEqual([])
    writeFileSync(path.join(path.dirname(readyFile!),`real-browser-${mode}.json`),JSON.stringify({Start:started,End:new Date().toISOString(),Simulation:true,JWTValidated:true,Mode:mode,NoAIRequests:true,First:first,Final:final,PageErrors:errors},null,2))
  })
}
