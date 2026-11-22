import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const conv = { id: 'history-a', title: '教材细胞呼吸', role_preset: 'tutor', created_at: '2026-10-11T12:00:00Z' }
const other = { ...conv, id: 'history-b', title: '另一个学习问题' }
const source = { citation_id: 'S1', document_id: 'doc', chunk_id: 'chunk', filename: '历史教材.pdf', page_range: '3', text: '', chunk_sha256: 'a'.repeat(64), truncated: false }
const steps = [{ seq: 1, type: 'run_started' }, { seq: 2, type: 'tool_started', tool: 'read_document_chunk' }, { seq: 3, type: 'tool_completed', tool: 'read_document_chunk' }, { seq: 4, type: 'run_completed' }]
const receipt = { run_id: '11111111-1111-4111-8111-111111111111', assistant_message_id: 'message-a', status: 'completed', scope: 'readonly_documents', model: 'explicit-simulated', sources: [source], steps }
const response = { receipts: [receipt], has_more: false }
const card = (page: import('@playwright/test').Page) => page.getByRole('button', { name: /S1.*历史教材/ })
const select = async (page: import('@playwright/test').Page, title = conv.title) => page.getByRole('button', { name: new RegExp(title + '.*学习辅导') }).click()

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/chat/conversations', r => r.fulfill({ json: [conv, other] }))
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { tutor: { name: '学习辅导' } } }))
  await page.route('**/api/v1/chat/conversations/history-*', r => {
    const item = r.request().url().endsWith('history-a') ? conv : other
    return r.fulfill({ json: { conversation_id: item.id, title: item.title, role_preset: 'tutor', messages: [{ id: item.id === conv.id ? 'message-a' : 'message-b', role: 'assistant', content: item.id === conv.id ? '细胞呼吸需要氧气 [S1]' : '另一对话的回答', created_at: conv.created_at }] } })
  })
  await page.route('**/api/v1/agent/conversations/*/receipts', r => r.fulfill({ json: r.request().url().includes('history-a') ? response : { receipts: [], has_more: false } }))
  await page.route('**/api/v1/agent/sources/**', r => r.fulfill({ json: { ...source, text: '当前核验后的引用原文' } }))
})

test('history restores durable sources and steps after refresh without generating', async ({ page }) => {
  let generated = 0
  await page.route('**/api/v1/agent/answer', r => { generated++; return r.abort() })
  await page.goto('/chat'); await select(page)
  await expect(card(page)).toBeVisible()
  await expect(page.getByText('资料 Agent · 只读', { exact: true })).toBeVisible()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('来源读取完成', { exact: true })).toBeVisible()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  await page.reload(); await select(page)
  await expect(card(page)).toBeVisible(); expect(generated).toBe(0)
})

test('receipt loading failure preserves history and retries without generation', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/agent/conversations/history-a/receipts', r => r.fulfill(++calls === 1 ? { status: 503, json: { detail: '模拟记录暂不可读' } } : { json: response }))
  await page.goto('/chat'); await select(page)
  await expect(page.locator('.chat-markdown')).toHaveText('细胞呼吸需要氧气 [S1]')
  await expect(page.getByText('执行记录加载失败', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新加载执行记录' }).click()
  await expect(card(page)).toBeVisible(); expect(calls).toBe(2)
})

test('late receipts cannot attach to a different conversation', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let started!: () => void
  const requested = new Promise<void>(resolve => { started = resolve })
  await page.route('**/api/v1/agent/conversations/history-a/receipts', async r => { started(); await gate; await r.fulfill({ json: response }).catch(() => {}) })
  await page.goto('/chat'); await select(page); await requested
  await select(page, other.title); release()
  await expect(page.locator('.chat-markdown')).toHaveText('另一对话的回答')
  await expect(card(page)).toHaveCount(0)
  await expect(page.getByText('执行记录加载失败', { exact: true })).toHaveCount(0)
})

test('new conversation cancels outstanding receipt ownership', async ({ page }) => {
  let release!: () => void; let started!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const requested = new Promise<void>(resolve => { started = resolve })
  await page.route('**/api/v1/agent/conversations/history-a/receipts', async r => { started(); await gate; await r.fulfill({ json: response }).catch(() => {}) })
  await page.goto('/chat'); await select(page); await requested
  await page.getByRole('button', { name: /新对话/ }).click(); release()
  await expect(page.getByRole('heading', { name: '今天想弄懂什么？' })).toBeVisible()
  await expect(card(page)).toHaveCount(0)
})

test('invalid or duplicated historical receipts do not impersonate completed tasks', async ({ page }) => {
  await page.route('**/api/v1/agent/conversations/history-a/receipts', r => r.fulfill({ json: { receipts: [receipt, receipt], has_more: false } }))
  await page.goto('/chat'); await select(page)
  await expect(page.getByText('执行记录加载失败', { exact: true })).toBeVisible()
  await expect(page.locator('.chat-markdown')).toContainText('细胞呼吸需要氧气')
  await expect(card(page)).toHaveCount(0)
})

test('historical citation checks current source version and can recover', async ({ page }) => {
  let reads = 0
  await page.route('**/api/v1/agent/sources/**', r => r.fulfill(++reads === 1 ? { status: 409, json: { detail: '来源内容已变更' } } : { json: { ...source, text: '当前核验后的引用原文' } }))
  await page.goto('/chat'); await select(page); await card(page).click()
  await expect(page.getByRole('dialog')).toContainText('来源内容已变更')
  await expect(page.getByRole('dialog')).not.toContainText('当前核验后的引用原文')
  await page.getByRole('dialog').getByRole('button', { name: '重新读取来源' }).click()
  await expect(page.getByRole('dialog')).toContainText('当前核验后的引用原文')
})

test('bounded history explicitly indicates older receipts are not loaded', async ({ page }) => {
  await page.route('**/api/v1/agent/conversations/history-a/receipts', r => r.fulfill({ json: { ...response, has_more: true } }))
  await page.goto('/chat'); await select(page)
  await expect(page.getByText('已加载最近100条执行记录，较早记录暂未显示。', { exact: true })).toBeVisible()
})

for (const width of [1440, 375]) for (const theme of ['light', 'dark']) {
  test(`restored receipts source and keyboard at ${width}px ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
    await page.addInitScript(t => localStorage.setItem('knowall-theme', t), theme)
    await page.goto('/chat')
    if (width === 375) await page.getByRole('button', { name: '打开对话历史' }).click()
    await select(page)
    if (width === 375) await expect(page.getByRole('dialog', { name: '对话历史' })).not.toBeVisible()
    await expect(card(page)).toBeVisible(); await card(page).focus(); await page.keyboard.press('Enter')
    await expect(page.getByRole('dialog', { name: '来源 S1' })).toContainText('当前核验后的引用原文')
    await expect(page.locator('.chat-agent-drawer .ant-drawer-content')).toHaveCSS('background-color', theme === 'dark' ? 'rgb(20, 38, 54)' : 'rgb(255, 255, 255)')
    await page.keyboard.press('Escape'); await expect(card(page)).toBeFocused()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    const dir = resolve('../docs/open-source-integration/native-agent-durable-2026-10-11/screenshots')
    await mkdir(dir, { recursive: true })
    await page.screenshot({ path: resolve(dir, `${process.env.AGENT_HISTORY_PHASE || 'dev'}-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  })
}
