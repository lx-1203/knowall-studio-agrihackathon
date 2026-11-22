import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mockDiscovery, discoveryDocument } from './mobile-discovery-fixture.mjs'
const require=createRequire(new URL('../../KnowAll_Studio/frontend/package.json',import.meta.url)), {chromium,expect}=require('@playwright/test')
const browser=await chromium.launch({channel:'chrome',headless:true}), theme=process.env.MOBILE_UI_THEME||'light', width=Number(process.env.MOBILE_UI_WIDTH||390), base=process.env.MOBILE_UI_URL||'http://localhost:8096', testCase=process.env.MOBILE_UI_CASE||'search'
const page=await browser.newPage({viewport:{width,height:844},colorScheme:theme}), state=await mockDiscovery(page), errors=[]
page.on('pageerror',e=>errors.push(e.message))
const button=name=>page.getByRole('button',{name,exact:true}), text=name=>page.getByText(name,{exact:true}).filter({visible:true}), input=()=>page.getByRole('textbox').first()
const shot=async name=>{await page.mouse.move(0,0);await page.waitForTimeout(400);await page.screenshot({path:fileURLToPath(new URL('../docs/patrol/ui04-01a12efd/'+name+'-'+theme+'-'+width+'.png',import.meta.url))})}
try {
 if(testCase==='before') {for(const path of ['search','knowledge']){await page.goto(base+'/'+path);await page.waitForTimeout(700);await shot('before-'+path)};process.exitCode=0}
 else if(testCase==='search-red') {await page.goto(base+'/search');await input().fill('细胞核');await input().press('Enter');await expect(text(discoveryDocument.filename)).toBeVisible();await button('打开命中正文').click();await expect(text('2 / 2')).toBeVisible()}
 else if(testCase==='tree-red') {await page.goto(base+'/knowledge');await text('细胞结构与生命活动').click();await expect(text('细胞膜')).toBeHidden();await button('展开 细胞结构').click();await expect(text('细胞膜')).toBeVisible()}
 else if(testCase==='search') {
  await page.goto(base+'/');await button('全文搜索').click();await expect(text('搜索内容')).toBeVisible();await input().fill('细胞核');await input().press('Enter')
  await expect(text(discoveryDocument.filename)).toBeVisible();expect(state.calls.find(c=>c.path==='/api/v1/search/').input.mode).toBe('keyword');await shot('after-search')
  await button('查看知识卡').click();await expect(text('保存遗传信息。'+'完整答案需要滚动查看。'.repeat(45))).toBeHidden();await button('查看答案').click();await expect(page.getByText(/^保存遗传信息。/)).toBeVisible();await button('收起答案').click();await button('返回搜索').click()
  await button('打开命中正文').click();await expect(text('2 / 2')).toBeVisible();await expect(text('已定位搜索结果')).toBeVisible();await shot('after-source');await button('上一段').click();await expect(text('1 / 2')).toBeVisible();await page.reload();await expect(text('1 / 2')).toBeVisible();await button('返回搜索结果').click();await expect(input()).toHaveValue('细胞核')
  state.faults.add('/api/v1/search/');await button('搜索').click();await expect(text('资料搜索暂时不可用，已保留其他结果。')).toBeVisible();await expect(text('细胞核有什么作用？')).toBeVisible();await shot('after-partial-error');state.faults.clear();await button('重试搜索').click();await expect(text(discoveryDocument.filename)).toBeVisible()
  state.delay='/api/v1/search/';await button('搜索').click();await button('清空搜索').click();await page.waitForTimeout(1900);await expect(text(discoveryDocument.filename)).toBeHidden();await expect(input()).toHaveValue('');state.delay=''
  await input().fill('不存在');await input().press('Enter');await expect(text('没有找到相关内容')).toBeVisible()
  await button('相关内容').click();await input().fill('细胞核');await input().press('Enter');await expect(page.getByText(/^相关资料片段 ·/).filter({visible:true})).toBeVisible();await expect(text(discoveryDocument.filename)).toBeVisible();await shot('after-related')
 }
 else if(testCase==='knowledge') {
  state.faults.add('/api/v1/knowledge/trees');await page.goto(base+'/knowledge');await expect(text('知识树加载失败，请重试。')).toBeVisible();await expect(text(discoveryDocument.filename)).toBeVisible();state.faults.clear();await button('重新加载知识树').click()
  await text('细胞结构与生命活动').click();await expect(text('细胞膜')).toBeHidden();await button('展开 细胞结构').click();await expect(text('细胞膜')).toBeVisible();await shot('after-knowledge');await button('查看 细胞核').click();await expect(page.getByTestId('modal-surface').getByText('储存遗传信息。'.repeat(30),{exact:true})).toBeVisible();await button('返回知识树').click();await button('收起 细胞结构').click();await expect(text('细胞膜')).toBeHidden()
  state.delay='/api/v1/knowledge/tree/tree-a';await text('细胞结构与生命活动').click();await text('细胞结构与生命活动').click();await text('遗传信息的传递').click();await expect(text('DNA复制与转录的完整过程及其在不同细胞中的表现')).toBeVisible();await page.waitForTimeout(1900);await expect(text('细胞结构')).toBeHidden();state.delay=''
  await button('打开资料 '+discoveryDocument.filename).click();await expect(text('1 / 2')).toBeVisible()
  state.paging=true;await page.goto(base+'/knowledge');await button('加载更多知识树').click();await expect(text('遗传信息的传递')).toBeVisible()
 }
 else if(testCase==='edge') {
  state.faults.add('/api/v1/search/');state.faults.add('/api/v1/flashcards/search');await page.goto(base+'/search');await input().fill('细胞核');await button('搜索').click();await expect(text('资料搜索暂时不可用，已保留其他结果。')).toBeVisible();await expect(text('知识卡搜索暂时不可用，已保留其他结果。')).toBeVisible();await expect(text('没有找到相关内容')).toBeHidden();await expect(input()).toHaveValue('细胞核');state.faults.clear()
  state.delay='/api/v1/search/';await button('重试搜索').click();await button('相关内容').click();await page.waitForTimeout(1900);await expect(text(discoveryDocument.filename)).toBeHidden();state.delay='';await button('关键词').click()
  state.hits=[{id:'document:discovery-doc',metadata:{doc_id:'discovery-doc',filename:discoveryDocument.filename}}];await button('搜索').click();await button('打开资料').click();await expect(text('1 / 2')).toBeVisible();expect(new URL(page.url()).searchParams.has('source')).toBe(false)
  await page.goto(base+'/material?id=discovery-doc&source=old-chunk');await expect(text('资料已更新，未找到命中片段；已打开当前资料。')).toBeVisible();await expect(text('1 / 2')).toBeVisible()
  state.hits=[{id:'missing',text:'来源尚未定位的片段'}];await page.goto(base+'/search');await input().fill('细胞核');await button('搜索').click();await expect(text('来源资料暂不可定位')).toBeVisible();await expect(button('打开命中正文')).toHaveCount(0)
  state.hits=null;state.searchPaging=true;await button('搜索').click();await expect(text('资料正文 · 10')).toBeVisible();await button('查看更多结果').click();await expect(text('资料正文 · 20')).toBeVisible();await shot('after-search-more')
  state.faults.add('/api/v1/knowledge/tree/tree-a');await page.goto(base+'/knowledge');await text('细胞结构与生命活动').click();await expect(text('这棵知识树暂时无法打开。')).toBeVisible({timeout:15000});state.faults.clear();await button('重试打开知识树').click();await expect(button('展开 细胞结构')).toBeVisible()
 }
 if(!['before','search-red','tree-red'].includes(testCase)){expect(errors).toEqual([]);expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);console.log(`PASS ${testCase}/${theme}/${width} requests=${state.calls.length}; isolated retrieval only, model calls=0`)}
}finally{await browser.close()}
