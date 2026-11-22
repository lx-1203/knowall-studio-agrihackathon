// Run only after approval to use the existing isolated Chrome test workflow.
// Test data only; every API call is intercepted, including the new note/progress sync.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const theme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390
const baseURL = process.env.MOBILE_UI_BASE_URL || 'http://localhost:8096'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width, height: 844 }, colorScheme: theme })
const page = await context.newPage(), errors = [], requests = []
const document = { id: 'calm-bio', filename: '细胞生物学 · 课程讲义.md', subject: '生物', material_kind: 'knowledge', status: 'ready', file_type: 'md', page_count: 24 }
const questions = { ...document, id: 'calm-quiz', filename: '细胞生物学 · 独立练习.md', material_kind: 'question_bank' }
const chunks = [
  { id: 'c1', index: 0, text: '# 第一章 细胞\n\n## 生命的基本单位\n\n细胞是生命活动的基本单位。从一片树叶到人的身体，生命都由细胞构成。\n\n每个细胞都在进行物质交换、能量转化和信息传递。这些微小而有序的活动，使生命得以延续。\n\n## 细胞膜的作用\n\n细胞膜把细胞内部与外部环境分开，同时允许特定物质通过。\n\n- 保护内部环境\n- 调节物质运输\n- 接收外界信号', page_range: '1–3' },
  { id: 'c2', index: 1, text: '# 第二章 遗传\n\nDNA 保存遗传信息。遗传信息通过复制、转录与翻译，在细胞中传递并表达。', page_range: '4–6' },
]
await page.addInitScript(() => {
  localStorage.setItem('knowall_token', 'local-calm-ui-test')
  localStorage.setItem('knowall_user', JSON.stringify({ id: 'calm-ui', username: '设计测试' }))
})
page.on('pageerror', error => errors.push(error.stack || error.message))
const syncStates = new Map()
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  requests.push({ path, method: route.request().method() })
  if (/\/state\/(note|text_progress)$/.test(path)) {
    const kind = path.split('/').at(-1)
    let value = syncStates.get(path) || { version: 1, account_id: 'calm-ui', document_id: document.id, kind, revision: 0, payload: null, source_version: null, current_source_version: 'calm-fixture-source', stale: false }
    if (route.request().method() === 'PUT') {
      const input = route.request().postDataJSON()
      expect(input.account_id).toBe('calm-ui')
      expect(input.base_revision).toBe(value.revision)
      value = { ...value, revision: value.revision + 1, payload: input.payload, source_version: input.source_version }
      syncStates.set(path, value)
    }
    return route.fulfill({ json: value })
  }
  if (route.request().method() !== 'GET') return route.fulfill({ status: 403, json: { detail: 'No writes in visual test' } })
  if (path.endsWith('/documents/')) return route.fulfill({ json: [document, questions] })
  if (path.endsWith('/chunks')) return route.fulfill({ json: chunks })
  if (path.endsWith('/pack')) return route.fulfill({ json: { id: 'calm-pack', stale: false, sections: [{ id: 's1', title: '细胞的结构与功能', notes: '细胞膜调节物质交换。细胞核保存遗传信息，细胞质为许多生化反应提供场所。', chunk_ids: ['c1'], flashcards: [{ id: 'card1', front: '细胞膜有哪些主要作用？', back: '保护内部环境、调节物质运输、接收外界信号。' }] }] } })
  return route.fulfill({ json: document })
})
const shot = async name => {
  const surface = page.locator('[data-testid="modal-surface"]:visible')
  if (await surface.count()) await expect.poll(() => surface.evaluate(element => {
    for (let node = element; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 0.999) return false
    return true
  })).toBeTruthy()
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${process.env.MOBILE_UI_EVIDENCE_DIR || 'ui-01a12efd'}/${name}-${theme}-${width}.png`, import.meta.url)), fullPage: true })
}
try {
  await page.goto(baseURL)
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible({ timeout: 120000 })
  await expect(page.getByRole('button', { name: '打开学习', exact: true })).toHaveCount(2)
  await shot('01-library')
  await page.getByText('题库资料', { exact: true }).first().click()
  await expect(page.getByRole('button', { name: '打开学习', exact: true })).toHaveCount(1)
  await expect(page.getByText(questions.filename, { exact: true })).toBeVisible()
  await page.getByText('全部', { exact: true }).click()
  await page.getByRole('button', { name: '打开学习', exact: true }).first().press('Enter')
  await expect(page.getByRole('button', { name: '目录', exact: true })).toBeVisible()
  const reading = await page.getByTestId('material-reading-content').boundingBox()
  expect(reading.height).toBeGreaterThan(844 * 0.45)
  await shot('02-reader')
  await page.getByRole('button', { name: '目录', exact: true }).click()
  await expect(page.getByRole('button', { name: '目录第 2 段', exact: true })).toBeVisible()
  await shot('03-directory')
  await page.getByRole('button', { name: '目录第 2 段', exact: true }).click()
  await expect(page.getByRole('button', { name: '关闭目录', exact: true })).toBeHidden()
  await expect(page.getByText('2 / 2', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await expect(page.getByRole('tab', { name: '知识点', exact: true })).toBeVisible()
  const assistantTab = await page.getByRole('tab', { name: '问答', exact: true }).boundingBox()
  expect(assistantTab.x + assistantTab.width).toBeLessThanOrEqual(width)
  const workspace = await page.getByTestId('modal-surface').boundingBox()
  expect(workspace.width).toBeGreaterThanOrEqual(width - 2)
  expect(workspace.height).toBeGreaterThanOrEqual(840)
  await shot('04-learning')
  await page.getByRole('tab', { name: '知识卡', exact: true }).click()
  await page.getByRole('button', { name: '显示答案', exact: true }).click()
  await expect(page.getByText('保护内部环境、调节物质运输、接收外界信号。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭学习工具', exact: true }).click()
  await expect(page.getByText('2 / 2', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '打开资料笔记', exact: true }).click()
  await page.getByRole('textbox', { name: '我的学习笔记', exact: true }).fill('目录定位后的笔记')
  await page.getByRole('button', { name: '返回阅读', exact: true }).click()
  await expect(page.getByText('这份资料的笔记', { exact: true })).toBeHidden()
  await page.reload()
  await expect(page.getByText('2 / 2', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '打开资料笔记', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记', exact: true })).toHaveValue('目录定位后的笔记')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
  expect(requests.every(request => request.method === 'GET' || request.method === 'PUT' && /\/state\/(note|text_progress)$/.test(request.path))).toBeTruthy()
  console.log(`PASS ${theme}/${width}: whole-row keyboard open, independent bank filter, reading space, directory, fullscreen tools, card reveal, return position and saved notes; only isolated note/progress sync writes, no AI or production writes.`)
} finally { await browser.close() }
