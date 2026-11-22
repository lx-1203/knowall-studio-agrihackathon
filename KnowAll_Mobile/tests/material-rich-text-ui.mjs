// Actual Expo web/iframe/KaTeX; isolated document responses. Native WebView is not tested here.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const colorScheme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390
const height = Number(process.env.MOBILE_UI_HEIGHT) || 844
const context = await browser.newContext({ viewport: { width, height }, colorScheme })
const page = await context.newPage()
const errors = [], requests = [], external = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', request => { if (/^https?:/.test(request.url()) && !/localhost|127\.0\.0\.1/.test(request.url())) external.push(request.url()) })
const first = ['# 数学与细胞', '', '阅读 **重点** 和公式：$\\frac{1}{\\sqrt{x^2+1}}$。', '', '- 核膜', '- DNA',
  '', '$$\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}$$', '',
  '| 类型 | 很长的第一列标题abcdefghijk | 很长的第二列标题abcdefghijk | 很长的第三列标题abcdefghijk |',
  '| --- | --- | --- | --- |', '| 数据 | A | B | C |', '',
  '$$'+Array(45).fill('x^2').join('+')+'$$', '', '价格 $20 和 $30。',
  '', '长分式：$\\frac{1}{'+Array(45).fill('x^2').join('+')+'}$',
  '', '~~~tex', '$\\frac{1}{2}$', '~~~', '', '<script>window.__uploadedScript=1</script>', '',
  '![说明](https://tracking.example/image.png)', '', '[外部资料](https://example.com)'].join('\n')
const bad = '# 原式保留\n\n之前 $\\unknown{<script>}$ 之后'
const long = '# 超长正文\n' + '完整段落内容'.repeat(10001)
const chunks = [first, bad, long, '最后一段普通文字，仍可选择与阅读。'].map((text, index) => ({ id: 'c'+(index+1), index, text, page_range: String(index+1) }))
const doc = { id: 'rich', filename: '数学笔记.md', material_kind: 'knowledge', subject: '数学', status: 'ready', file_type: 'md' }
await page.addInitScript(() => {
  localStorage.setItem('knowall_token', 'local-ui-test-token')
  localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  requests.push({ path, method: route.request().method(), auth: route.request().headers()['authorization'] })
  const result = path.endsWith('/documents/') ? [doc] : path.endsWith('/chunks') ? chunks : path.endsWith('/documents/rich') ? doc : null
  await route.fulfill({ json: result })
})
const frame = () => page.frameLocator('iframe[title="资料排版正文"]')
const screenshot = async name => page.screenshot({ path: fileURLToPath(new URL('../docs/patrol/'+(process.env.MOBILE_UI_EVIDENCE_ROUND || 'round07')+'-'+name+'-'+colorScheme+'-'+width+'.png', import.meta.url)), fullPage: true })
const next = () => page.getByRole('button', { name: '下一段', exact: true }).click()
try {
  await page.goto('http://localhost:8096')
  await expect(page.getByRole('button', { name: '打开学习', exact: true })).toBeVisible({ timeout: 120000 })
  await page.getByRole('button', { name: '打开学习', exact: true }).click()
  await expect(frame().getByRole('heading', { name: '数学与细胞', exact: true })).toBeVisible()
  await expect(frame().locator('math mfrac')).toHaveCount(2)
  await expect(frame().locator('math mtable')).toHaveCount(1)
  await expect(frame().locator('body')).toContainText('价格 $20 和 $30。')
  await expect(frame().locator('pre')).toContainText('$\\frac{1}{2}$')
  expect(await frame().locator('body').evaluate(body => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy()
  expect(await frame().locator('.wide').evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy()
  expect(await frame().locator('.katex-display').last().evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy()
  expect(await frame().locator('.math-inline').last().evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy()
  expect(await frame().locator('body').evaluate(() => window.__uploadedScript)).toBeUndefined()
  await expect(frame().locator('script, img, a')).toHaveCount(0)
  await expect.poll(() => frame().locator('body').evaluate(() => document.fonts.status)).toBe('loaded')
  await expect.poll(() => frame().locator('body').evaluate(body => body.getBoundingClientRect().bottom <= innerHeight)).toBeTruthy()
  await screenshot('reading')
  await page.getByRole('button', { name: '放大字号', exact: true }).click()
  await expect(frame().locator('body')).toHaveCSS('font-size', '20px')
  await context.setOffline(true)
  await page.getByRole('button', { name: '原始文字', exact: true }).click()
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect(page.getByText(first, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '排版阅读', exact: true }).click()
  await expect(frame().getByRole('heading', { name: '数学与细胞', exact: true })).toBeVisible()
  await expect.poll(() => frame().locator('body').evaluate(() => document.fonts.status)).toBe('loaded')
  await next()
  await expect(page.getByText('有 1 处公式未能排版，已保留原式；可切换原始文字查看。')).toBeVisible()
  await expect(frame().locator('.math-error')).toContainText('$\\unknown{<script>}$')
  await screenshot('formula-fallback')
  // A renderer height failure must expose the whole source, not clip the rest of the chapter.
  await frame().locator('body').evaluate(body => { body.style.height = '70001px' })
  await expect(page.getByText('排版显示未完成，已显示完整原始文字。')).toBeVisible()
  await expect(page.getByText(bad, { exact: true })).toBeVisible()
  await next()
  await expect(page.getByText('这段正文较长，已显示完整原始文字。')).toBeVisible()
  await expect(page.getByText(long, { exact: true })).toBeVisible()
  await next()
  await expect(page.getByText(chunks[3].text, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(external).toEqual([]); expect(errors).toEqual([])
  expect(requests.every(request => request.method === 'GET' && request.auth === 'Bearer local-ui-test-token')).toBeTruthy()
  console.log('PASS ('+colorScheme+', '+width+'px): actual KaTeX nested fraction/matrix, Markdown/table/code, local horizontal scroll, offline font reload, font size/raw/segment switch, invalid formula verbatim, oversize/height fallback, inert HTML/images/links; no external request, paid generation, overflow or page errors. Browser supplement only.')
} finally { await browser.close() }
