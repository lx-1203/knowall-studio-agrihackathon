import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page, Locator } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
const evidencePrefix = process.env.OSS_EVIDENCE_PREFIX || 'round-10'
import { content, feedback, chunks, pack, session, result } from './material-content-fixture'

async function routes(page: Page) {
  await page.route('**/api/v1/documents/**', route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    const uri = new URL(route.request().url()).pathname
    if (uri.endsWith('/chunks')) return route.fulfill({ json: chunks })
    if (uri.endsWith('/raw')) return route.fulfill({ contentType: 'text/plain', body: chunks[0].text })
    return route.fulfill({ json: { id: 'content', filename: '原生学习.md', file_type: 'md', status: 'ready', subject: '数学', page_count: 2 } })
  })
  await page.route('**/api/v1/learning/**', route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    const uri = new URL(route.request().url()).pathname
    return route.fulfill({ json: uri.endsWith('/pack') ? pack : uri.endsWith('/session') ? session : result })
  })
  await page.setViewportSize({ width: 1600, height: 1000 })
}
async function open(page: Page) { await page.goto('/materials/content'); await page.getByRole('button', { name: /第一章 原生学习/ }).click() }
async function native(locator: Locator) {
  await expect(locator.locator('.katex')).toHaveCount(2)
  await expect(locator).toContainText('金额 $5 和 $10')
  await expect(locator.locator('p > code')).toHaveText('$raw$')
  await expect(locator.locator('table td')).toHaveText(['数学', '50%'])
  await expect(locator.locator('pre code')).toHaveText('def learn(value):\n    return value + 1\n')
  await expect(locator.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
}
test.beforeEach(async ({ page }) => routes(page))

test('actual material text renders native content while chapter source and resume remain unchanged', async ({ page }) => {
  await open(page); const article=page.getByRole('article', { name: '资料正文' })
  await native(article)
  await article.screenshot({ path: path.resolve(`../docs/open-source-integration/${evidencePrefix}-material-text.png`) })
  await page.getByRole('button', { name: /下一段/ }).click()
  await expect(article).toContainText('当前第二段原文'); await expect(article.locator('.katex')).toHaveCount(0)
  await page.reload(); await expect(article).toContainText('当前第二段原文')
  await expect(page.getByRole('button', { name: /第二章 当前来源/ })).toHaveAttribute('aria-current','location')
})

test('actual assistant uses native reply and preserves literal tutor/context/Bearer payload', async ({ page }) => {
  let sent: unknown
  await page.route('**/api/v1/chat/assistant', route => { expect(route.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`); sent=route.request().postDataJSON(); return route.fulfill({ json: { message: content } }) })
  await open(page); await page.getByRole('tab', { name: '问答', exact: true }).click()
  await page.getByRole('textbox', { name: '向当前资料提问' }).fill('  解释公式，不改原文  ')
  await page.getByRole('button', { name: /发送问题/ }).click()
  await native(page.locator('.material-assistant-reply'))
  expect(sent).toEqual({ message:'解释公式，不改原文',role_preset:'tutor',knowledge_context:chunks[0].text })
})

test('actual chapter notes/question/partial feedback preserve original answer and source location', async ({ page }) => {
  let sent: { answer?: string; question_id?: string; time_spent_ms?: number } | undefined
  await page.route('**/api/v1/learning/packs/content-pack/answers', route => { sent=route.request().postDataJSON(); return route.fulfill({ json: result }) })
  await open(page); await page.getByRole('tab', { name: '练习', exact: true }).click(); await page.getByRole('button',{name:'章节练习',exact:true}).click()
  const notes=page.locator('.ant-card').filter({has:page.getByText('章节笔记',{exact:true})});await native(notes)
  const q=page.locator('.ant-card').filter({has:page.getByText('随堂练习',{exact:true})});await native(q)
  await page.getByRole('textbox',{name:'你的答案'}).fill(' 1;原答 ');await page.getByRole('button',{name:'提交答案',exact:true}).click()
  await expect(q).toContainText('得分：50%');await expect(q.locator('.ant-alert .katex')).toHaveCount(1)
  await expect(q.locator('.ant-alert pre code')).toHaveText('const score = 0.5;\n')
  expect(sent).toMatchObject({question_id:'content-q',answer:' 1;原答 '});expect(sent!.time_spent_ms).toBeGreaterThanOrEqual(0)
  await page.getByRole('button',{name:'定位来源原文',exact:true}).click();await expect(page.getByRole('article',{name:'资料正文'})).toContainText('第一章 原生学习')
  await expect(page.getByRole('button',{name:/第一章 原生学习/})).toHaveAttribute('aria-current','location')
})

test('actual study card front/back remain concealed until reveal and keep FSRS rating payload', async ({ page }) => {
  let rating: unknown
  await page.route('**/api/v1/learning/packs/content-pack/session', route => { expect(route.request().postDataJSON()).toEqual({mode:'smart',limit:20}); return route.fulfill({json:{items:[session.items[1]]}}) })
  await page.route('**/api/v1/learning/packs/content-pack/review', route => { rating=route.request().postDataJSON(); return route.fulfill({json:{ok:true}}) })
  await open(page);await page.getByRole('tab',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'智能复习',exact:true}).click()
  const q=page.locator('.ant-card').filter({has:page.getByText('复习卡片',{exact:true})});await native(q)
  await expect(q).not.toContainText('解释');await page.getByRole('button',{name:'显示答案',exact:true}).click()
  await expect(q.locator('.katex')).toHaveCount(3);await expect(q.locator('pre code').last()).toHaveText('const score = 0.5;\n')
  await page.getByRole('button',{name:'良好',exact:true}).click();expect(rating).toEqual({card_id:'content-card',rating:3})
  await expect(page.getByText('本轮练习已完成',{exact:true})).toBeVisible()
})

test('material knowledge notes/cards share native content and keep source/reveal controls', async ({ page }) => {
  await open(page);await page.getByRole('tab',{name:'知识点',exact:true}).click();await page.getByText('学习章节',{exact:true}).click()
  await native(page.locator('.material-tools .ant-collapse-content .material-markdown'))
  await page.getByRole('button',{name:'阅读对应原文',exact:true}).click();await expect(page.getByRole('article',{name:'资料正文'})).toContainText('第一章 原生学习')
  await page.getByRole('tab',{name:'知识卡',exact:true}).click();const card=page.locator('.material-knowledge-card');await native(card)
  await expect(page.getByTestId('material-card-answer')).toHaveCount(0)
  await card.getByRole('button',{name:'显示答案',exact:true}).click();await expect(page.getByTestId('material-card-answer').locator('.katex')).toHaveCount(1)
  await card.getByRole('button',{name:'收起答案',exact:true}).click();await expect(page.getByTestId('material-card-answer')).toHaveCount(0)
})

test('assistant ignores an old chapter reply while new reply uses current source', async ({ page }) => {
  let release!: () => void;const delayed=new Promise<void>(r=>release=r);let arrived!:()=>void;const started=new Promise<void>(r=>arrived=r)
  await page.route('**/api/v1/chat/assistant', async route => { arrived();await delayed;await route.fulfill({json:{message:'旧回复 '+feedback}}) })
  await open(page);await page.getByRole('tab',{name:'问答',exact:true}).click();await page.getByRole('textbox',{name:'向当前资料提问'}).fill('解释旧段')
  await page.getByRole('button',{name:/发送问题/}).click();await started
  await page.getByRole('button',{name:/第二章 当前来源/}).click();release()
  await expect(page.locator('.material-assistant-source')).toContainText('p2');await expect(page.locator('.material-assistant-reply')).toHaveCount(0)
  await expect(page.getByRole('textbox',{name:'向当前资料提问'})).toBeEnabled()
  await page.unroute('**/api/v1/chat/assistant');await page.route('**/api/v1/chat/assistant',route=>route.fulfill({json:{message:feedback}}))
  await page.getByRole('button',{name:/发送问题/}).click();await expect(page.locator('.material-assistant-reply .katex')).toHaveCount(1)
  await expect(page.locator('.material-assistant-reply')).not.toContainText('旧回复')
})

test('shared module failure preserves material original text and practice controls', async ({ page }) => {
  // Start in text mode so aborting the content module tests its own boundary,
  // rather than aborting the separately imported original-file preview.
  await page.route('**/api/v1/documents/content',route=>route.fulfill({json:{id:'content',filename:'学习正文',file_type:'source',status:'ready',subject:'数学'}}))
  await page.route('**/src/components/MarkdownContent.tsx*',route=>route.abort('failed'))
  await open(page);await expect(page.getByRole('article',{name:'资料正文'}).locator('.learning-content-fallback')).toHaveText(chunks[0].text)
  await page.getByRole('tab',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'章节练习',exact:true}).click()
  await page.getByRole('textbox',{name:'你的答案'}).fill('加载失败仍可答');await expect(page.getByRole('button',{name:'提交答案',exact:true})).toBeEnabled()
})

test('failed original preview module leaves the actual material directory and native text mode available', async ({ page }) => {
  await page.route('**/src/components/previews/MarkdownPreview.tsx*',route=>route.abort('failed'))
  await page.goto('/materials/content')
  await expect(page.getByText('原文件预览加载失败',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:/第一章 原生学习/}).click()
  await native(page.getByRole('article',{name:'资料正文'}))
  await expect(page.getByRole('button',{name:/下一段/})).toBeEnabled()
})

test('tall fractions do not acquire a vertical scrollbar and wide formulas remain horizontally scrollable', async ({ page }) => {
  await page.goto('/dashboard')
  await page.evaluate(async () => (await import('/.tmp/practiceHarness.tsx')).show('$$\n\\frac{\\frac{a}{b}}{\\frac{c}{d}}\n$$\n\n$$\n'+Array.from({length:40},()=> 'x^2').join('+')+'\n$$'))
  const display=page.locator('#practice-test-mount .katex-display')
  await expect(display).toHaveCount(2)
  const metrics=await display.evaluateAll(nodes=>nodes.map(node=>({clientHeight:node.clientHeight,scrollHeight:node.scrollHeight,clientWidth:node.clientWidth,scrollWidth:node.scrollWidth,overflowY:getComputedStyle(node).overflowY})))
  fs.writeFileSync(path.resolve(`../docs/open-source-integration/${evidencePrefix}-math-overflow-${process.env.OSS_EVIDENCE_STAGE||'current'}.json`),JSON.stringify(metrics,null,2))
  expect(metrics[0].scrollHeight).toBeLessThanOrEqual(metrics[0].clientHeight)
  expect(metrics[1].scrollWidth).toBeGreaterThan(metrics[1].clientWidth)
  await expect(display.last()).toHaveCSS('overflow-x','auto')
})

test('assistant preserves its existing 20000-character context limit', async ({ page }) => {
  const text=chunks[0].text+'z'.repeat(21000);let sent: {knowledge_context:string}|undefined
  await page.route('**/api/v1/documents/content/chunks',route=>route.fulfill({json:[{...chunks[0],text}]}))
  await page.route('**/api/v1/chat/assistant',route=>{sent=route.request().postDataJSON();return route.fulfill({json:{message:feedback}})})
  await open(page);await page.getByRole('tab',{name:'问答',exact:true}).click();await expect(page.locator('.material-assistant-source')).toContainText('20,000')
  await page.getByRole('textbox',{name:'向当前资料提问'}).fill('长文问题');await page.getByRole('button',{name:/发送问题/}).click()
  await expect(page.locator('.material-assistant-reply')).toContainText('解释')
  expect(sent?.knowledge_context).toBe(text.slice(0,20000))
})

test('dark narrow material confines native code/table/math without changing reading navigation', async ({ page }) => {
  await page.addInitScript(()=>localStorage.setItem('knowall-theme','dark'));await page.setViewportSize({width:375,height:812});await open(page)
  const article=page.getByRole('article',{name:'资料正文'});await expect(article.locator('pre code span').filter({hasText:/^def$/}).first()).toHaveCSS('color','rgb(86, 156, 214)')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await article.screenshot({path:path.resolve(`../docs/open-source-integration/${evidencePrefix}-material-dark.png`)})
  await page.getByRole('button',{name:/下一段/}).click();await expect(article).toContainText('当前第二段原文')
})
