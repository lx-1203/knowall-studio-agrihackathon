import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync,writeFileSync } from 'node:fs'
import path from 'node:path'
const file=process.env.LEARNING_BLANK_NATIVE_READY
const ready=file?JSON.parse(readFileSync(file,'utf8')):null
test.skip(!ready,'private real JWT SQLite server required')
for(const mode of ['fill_blank','retry','repair'])test(`native fill blank menu input persistence ${mode}`,async({page,request})=>{
 const Start=new Date().toISOString(),errors:string[]=[],headers={Authorization:'Bearer '+ready.Tokens.A};page.on('pageerror',e=>errors.push(e.message))
 expect((await request.post(ready.BackendURL+'/__test/reset',{data:{Mode:mode}})).status()).toBe(200)
 await stubBackend(page);await page.route('**/api/v1/documents**',r=>r.fulfill({json:[{id:'doc-1',filename:'隔离学习资料.md'}]}))
 await page.addInitScript(({token})=>{localStorage.setItem('knowall_token',token);localStorage.setItem('knowall_user',JSON.stringify({id:'A',username:'learning-A',email:'A@09b6.invalid'}))},{token:ready.Tokens.A})
 let failed=false
 await page.route('**/api/v1/learning/**',r=>{
  if(mode==='retry'&&!failed&&r.request().url().endsWith('/answers')){failed=true;return r.fulfill({status:503,json:{detail:'explicit simulated pre-send failure'}})}
  return r.continue({url:ready.BackendURL+new URL(r.request().url()).pathname+new URL(r.request().url()).search})
 })
 const select=async()=>{await page.getByRole('combobox',{name:'学习文档'}).click();await page.getByText('隔离学习资料.md',{exact:true}).last().click()}
 await page.goto('/learning');await select();await page.getByRole('spinbutton',{name:'习题数量'}).fill('1');await page.getByRole('spinbutton',{name:'卡片数量'}).fill('0');await page.getByRole('combobox',{name:'题型',exact:true}).click();await page.locator('.ant-select-item-option-content').filter({hasText:/^填空$/}).click();await page.keyboard.press('Escape')
 const generated=page.waitForResponse(r=>r.url().endsWith('/generate')&&r.request().method()==='POST');await page.getByRole('button',{name:'生成学习包',exact:true}).click();const response=await generated;expect(response.status()).toBe(200);const pack=await response.json();const pid=pack.id,qid=pack.sections[0].questions[0].id
 const begin=async(count:number)=>{const p=page.waitForResponse(r=>r.url().endsWith('/session'));await page.getByRole('button',{name:'章节练习',exact:true}).click();const data=await(await p).json();const q=data.items[0].question;expect(q.blank_count).toBe(count||undefined);for(const key of ['answer','correct_answer','blanks','scoring_points','analysis'])expect(q[key]).toBeUndefined()}
 await begin(2);await page.getByRole('textbox',{name:'第2空',exact:true}).fill('氧')
 if(mode==='retry'){await page.getByRole('button',{name:/提交答案$/}).click();await expect(page.getByText('提交失败，答案已保留，请重试。')).toBeVisible();await expect(page.getByRole('textbox',{name:'第2空',exact:true})).toHaveValue('氧')}
 const partial=page.waitForResponse(r=>r.url().endsWith('/answers'));await page.getByRole('button',{name:/提交答案$/}).click();expect((await(await partial).json()).score_ratio).toBe(.5);await expect(page.getByText('得分：50%',{exact:true})).toBeVisible()
 await page.screenshot({path:path.join(path.dirname(file!),`native-${mode}-partial.png`),fullPage:true});await page.reload();await select()
 if(mode==='repair'){await page.getByRole('tab',{name:'质检与缺漏'}).click();await page.getByRole('button',{name:'修复题目',exact:true}).click();await page.getByRole('textbox',{name:'正确答案',exact:true}).fill('新答案');await page.getByRole('button',{name:'保存并质检',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await page.getByRole('tab',{name:'学习与练习'}).click()}
 await begin(mode==='repair'?0:2)
 if(mode==='repair')await page.getByRole('textbox',{name:'你的答案',exact:true}).fill('新答案');else{await page.getByRole('textbox',{name:'第1空',exact:true}).fill('氢');await page.getByRole('textbox',{name:'第2空',exact:true}).fill('氧')}
 const full=page.waitForResponse(r=>r.url().endsWith('/answers'));await page.getByRole('button',{name:/提交答案$/}).click();expect((await(await full).json()).score_ratio).toBe(1);await expect(page.getByText('得分：100%',{exact:true})).toBeVisible()
 const denied=await request.post(ready.BackendURL+`/api/v1/learning/packs/${pid}/session`,{headers:{Authorization:'Bearer '+ready.Tokens.B},data:{mode:'practice'}});expect(denied.status()).toBe(404)
 const final=await(await request.get(ready.BackendURL+'/__test/state')).json();const answers=final.Answers.filter((a:any)=>a.QuestionID===qid);expect(answers.map((a:any)=>a.Answer)).toEqual([';氧',mode==='repair'?'新答案':'氢;氧']);expect(final.AIRequests).toEqual([]);expect(final.ProviderRequests).toHaveLength(1);expect(errors).toEqual([])
 await page.screenshot({path:path.join(path.dirname(file!),`native-${mode}-full.png`),fullPage:true});writeFileSync(path.join(path.dirname(file!),`real-browser-${mode}.json`),JSON.stringify({Start,End:new Date().toISOString(),Final:final,PageErrors:errors,RetryFirstRequestExplicitlyMocked:mode==='retry',DocumentListingMocked:true},null,2))
})
