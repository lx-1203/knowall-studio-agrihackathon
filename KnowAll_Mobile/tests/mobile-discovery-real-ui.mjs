import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import fs from 'node:fs/promises'
const require=createRequire(new URL('../../KnowAll_Studio/frontend/package.json',import.meta.url)),{chromium,expect}=require('@playwright/test')
const browser=await chromium.launch({channel:'chrome',headless:true}),theme=process.env.MOBILE_UI_THEME||'light',base=process.env.MOBILE_UI_URL||'http://localhost:8096',api='http://127.0.0.1:8110'
const page=await browser.newPage({viewport:{width:theme==='dark'?320:390,height:844},colorScheme:theme}),requests=[],errors=[]
const text=value=>page.getByText(value,{exact:true}).filter({visible:true}),button=value=>page.getByRole('button',{name:value,exact:true})
await expect.poll(async()=>{try{return(await page.request.get(api+'/__test/health')).ok()}catch{return false}},{timeout:60000}).toBe(true)
const response=await page.request.post(api+'/api/v1/auth/login',{data:{username:'discovery-user',password:'LocalDiscovery_2026!'}});expect(response.ok()).toBe(true);const login=await response.json()
await page.addInitScript(login=>{localStorage.setItem('knowall_token',login.access_token);localStorage.setItem('knowall_user',JSON.stringify(login.user))},login)
page.on('pageerror',e=>errors.push(e.message))
await page.route('**/api/**',async route=>{
 const url=new URL(route.request().url()),method=route.request().method()
 if(method!=='GET' && !(method==='POST'&&url.pathname==='/api/v1/search/'&&route.request().postDataJSON()?.mode==='keyword') && !(method==='PUT'&&/\/state\/(text_progress|note)$/.test(url.pathname)))throw Error('Unexpected non-read action '+method+' '+url.pathname)
 const result=await route.fetch({url:api+url.pathname+url.search});requests.push({path:url.pathname,method,status:result.status()});await route.fulfill({response:result})
})
const shot=async name=>{await page.mouse.move(0,0);await page.waitForTimeout(400);await page.screenshot({path:fileURLToPath(new URL('../docs/patrol/ui04-01a12efd/http-'+name+'-'+theme+'.png',import.meta.url))})}
try{
 await page.goto(base+'/search');await page.getByLabel('搜索内容关键词',{exact:true}).fill('细胞核');await button('搜索').click()
 await expect(text('细胞生物学 · 阅读笔记.md')).toBeVisible();await expect(text('细胞核有什么作用？')).toBeVisible();await expect(page.getByText(/别人的|其他账号的/)).toHaveCount(0);await shot('search')
 await button('查看知识卡').click();await button('查看答案').click();await expect(text('保存遗传信息。')).toBeVisible();await button('返回搜索').click()
 await button('打开命中正文').click();await expect(text('2 / 2')).toBeVisible();await expect(text('已定位搜索结果')).toBeVisible();await shot('source')
 await expect.poll(async()=>{const r=await page.request.get(api+'/api/v1/learning/documents/discovery-doc/state/text_progress',{headers:{Authorization:'Bearer '+login.access_token}});expect(r.ok()).toBe(true);return(await r.json()).payload?.chunk_index}).toBe(1)
 await button('放大字号').click();await button('上一段').click();await expect(text('1 / 2')).toBeVisible();await page.reload();await expect(text('1 / 2')).toBeVisible()
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('material-progress:discovery-user:discovery-doc')).fontSize)).toBe(20)
 await button('返回搜索结果').click();await expect(page.getByLabel('搜索内容关键词',{exact:true})).toHaveValue('细胞核')
 await page.goto(base+'/knowledge');await expect(text('细胞结构与生命活动')).toBeVisible();await expect(page.getByText('其他账号的私密知识树',{exact:true})).toHaveCount(0)
 await text('细胞结构与生命活动').click();await expect(text('细胞膜')).toBeHidden();await button('展开 细胞结构').click();await expect(text('细胞膜')).toBeVisible();await shot('knowledge')
 await button('打开资料 细胞生物学 · 阅读笔记.md').click();await expect(text('1 / 2')).toBeVisible()
 await expect.poll(async()=>{const r=await page.request.get(api+'/api/v1/learning/documents/discovery-doc/state/text_progress',{headers:{Authorization:'Bearer '+login.access_token}});expect(r.ok()).toBe(true);return(await r.json()).payload?.chunk_index}).toBe(0)
 const health=await(await page.request.get(api+'/__test/health')).json();expect(health.external_calls).toBe(0);expect(health.card_sql_fallbacks).toBeGreaterThan(0);expect(requests.filter(r=>r.status>=400)).toEqual([]);expect(errors).toEqual([])
 const bundles=await page.evaluate(()=>Array.from(document.scripts).map(s=>s.src).filter(s=>s.includes('/_expo/static/')))
 await fs.writeFile(fileURLToPath(new URL('../docs/patrol/ui04-01a12efd/http-trace-'+theme+'.json',import.meta.url)),JSON.stringify({requests,health,bundles},null,2))
 console.log(`PASS real discovery HTTP/JWT/SQLite ${theme}: private SQL search/card/tree, source navigation/restore and cloud position; requests=${requests.length}; external_calls=0`)
}finally{await browser.close()}
