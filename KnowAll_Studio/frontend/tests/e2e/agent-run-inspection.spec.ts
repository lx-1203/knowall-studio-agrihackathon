import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
const at = '2026-10-11T15:24:57Z'
const steps = [
  { seq: 1, type: 'run_started', at: '2026-10-11T15:24:56Z' },
  { seq: 2, type: 'step_started', at },
  { seq: 3, type: 'tool_started', tool: 'search_documents', call_id: 'search', at },
  { seq: 4, type: 'tool_completed', tool: 'search_documents', call_id: 'search', at },
]
const source = { citation_id: 'S1', document_id: 'doc', chunk_id: 'chunk', filename: '核对教材.pdf', page_range: '7', text: '', chunk_sha256: 'a'.repeat(64), truncated: false }
const record = { run_id: 'inspection-run', conversation_id: null, assistant_message_id: null, status: 'failed', scope: 'readonly_documents', model: 'actual/model-for-inspection', error_code: 'MODEL_REQUEST_FAILED', sources: [], steps: [...steps, { seq: 5, type: 'run_failed', at }], execution_mode: 'background', cancel_requested: false, recovery_state: 'terminal', created_at: '2026-10-11T15:24:56Z' }
async function observe(page: import('@playwright/test').Page) {
  await page.addInitScript(() => localStorage.setItem('knowall_agent_observed_run:e2e-user', JSON.stringify({ run_id: 'inspection-run' })))
}
for (const status of ['failed', 'cancelled', 'partial', 'waiting_user', 'running']) test(`saved ${status} inspection displays actual steps/model/time without claiming live execution`, async ({ page }) => {
  await observe(page); const posts: string[] = []
  page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/api/v1/agent/')) posts.push(r.url()) })
  const end = status === 'cancelled' ? [{ seq: 5, type: 'run_cancelled', at }] : status === 'failed' ? [{ seq: 5, type: 'run_failed', at }] : []
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, status, steps: [...steps, ...end] } }))
  await page.goto('/chat'); const region = page.getByRole('region', { name: '上次资料任务' })
  await expect(region.getByText('已保存的任务详情', { exact: true })).toBeVisible()
  const summary = region.getByText('查看已保存步骤', { exact: true }); await summary.focus(); await page.keyboard.press('Enter')
  await expect(region.getByText('资料检索完成', { exact: true })).toBeVisible()
  await expect(region.locator('.chat-agent-steps')).toContainText('actual/model-for-inspection')
  await expect(region.locator('.chat-agent-inspection-meta time').last()).toHaveAttribute('dateTime', at)
  await expect(region.locator('.chat-agent-inspection-read time')).toHaveAttribute('dateTime', /2026-/)
  await expect(region.locator('.chat-agent-live')).toHaveCount(0)
  await expect(region.getByText('依据资料回答完成', { exact: true })).toHaveCount(0)
  await expect(region.getByRole('button', { name: /S1/ })).toHaveCount(0)
  expect(posts).toEqual([])
})
test('historical saved events without timestamps remain readable and do not invent a time', async ({ page }) => {
  await observe(page)
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, created_at: undefined, steps: steps.map(({ at: ignored, ...s }) => s) } }))
  await page.goto('/chat'); await page.getByText('查看已保存步骤', { exact: true }).click()
  await expect(page.getByRole('region', { name: '上次资料任务' }).getByText('资料检索完成', { exact: true })).toBeVisible()
  await expect(page.locator('.chat-agent-inspection-meta time')).toHaveCount(0)
  await expect(page.getByText('历史时间未记录', { exact: true })).toBeVisible()
})
test('failed reread hides stale snapshot and restores actual saved details after retry', async ({ page }) => {
  await observe(page); let reads = 0
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill(++reads === 2 ? { status: 503, json: { detail: '核对记录暂时不可用' } } : { json: record }))
  await page.goto('/chat'); await expect(page.getByText('已保存的任务详情', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新读取运行状态' }).click()
  await expect(page.getByText('核对记录暂时不可用', { exact: true })).toBeVisible()
  await expect(page.getByText('查看已保存步骤', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '重新读取运行状态' }).click()
  await page.getByText('查看已保存步骤', { exact: true }).click()
  await expect(page.getByText('资料检索完成', { exact: true })).toBeVisible()
})
test('malformed saved steps never render an inspection receipt', async ({ page }) => {
  await observe(page)
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, steps: [{ seq: 2, type: 'tool_completed', at }] } }))
  await page.goto('/chat'); await expect(page.getByText('运行记录格式异常，请重新读取。', { exact: true })).toBeVisible()
  await expect(page.getByText('查看已保存步骤', { exact: true })).toHaveCount(0)
})
test('saved partial source uses current permissions and retries without starting a task', async ({ page }) => {
  await observe(page); let reads = 0; let posts = 0
  page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/api/v1/agent/')) posts++ })
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, status: 'partial', sources: [source], steps } }))
  await page.route('**/api/v1/agent/sources/chunk?*', r => r.fulfill(++reads === 1 ? { status: 503, json: { detail: '来源核对暂时不可用' } } : { status: 404, json: { detail: '来源已移除或不可读取' } }))
  await page.goto('/chat'); const button = page.getByRole('button', { name: /S1.*核对教材/ }); await button.click()
  await expect(page.getByRole('dialog', { name: '来源 S1' })).toContainText('来源核对暂时不可用')
  await page.getByRole('button', { name: '重新读取来源' }).click()
  await expect(page.getByRole('dialog', { name: '来源 S1' })).toContainText('来源已移除或不可读取')
  const focusBeforeEscape = await page.evaluate(() => ({ tag: document.activeElement?.tagName, text: document.activeElement?.textContent?.slice(0, 80) }))
  await test.info().attach('focus-before-escape', { body: JSON.stringify(focusBeforeEscape), contentType: 'application/json' })
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog', { name: '来源 S1' })).toBeHidden(); await expect(button).toBeFocused(); expect(posts).toBe(0)
})
test('switching inspected run destroys its stale source drawer and snapshot', async ({ page }) => {
  await page.route('**/api/v1/agent/runs?*', r => r.fulfill({ json: { runs: [{ ...record, sources: [source] }, { ...record, run_id: 'inspection-B', model: 'actual/model-B', sources: [] }], has_more: false } }))
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, sources: [source] } }))
  await page.route('**/api/v1/agent/runs/inspection-B', r => r.fulfill({ json: { ...record, run_id: 'inspection-B', model: 'actual/model-B', steps: steps.slice(0, 2) } }))
  await page.route('**/api/v1/agent/sources/chunk?*', r => r.fulfill({ json: { ...source, text: '旧任务来源正文' } }))
  await page.goto('/chat'); await page.getByRole('button', { name: '查看任务记录' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '核对运行状态' }).first().click()
  await page.getByRole('button', { name: /S1.*核对教材/ }).click(); await expect(page.getByText('旧任务来源正文', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape'); await page.getByRole('button', { name: '查看任务记录' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '核对运行状态' }).nth(1).click()
  await page.getByText('查看已保存步骤', { exact: true }).click()
  await expect(page.locator('.chat-agent-recovery')).toContainText('actual/model-B')
  await expect(page.getByRole('button', { name: /S1.*核对教材/ })).toHaveCount(0)
  await expect(page.getByText('旧任务来源正文', { exact: true })).toHaveCount(0)
})
for (const pendingRetry of [true, false]) test(`source retry retains keyboard close while ${pendingRetry ? 'pending' : 'successful'}`, async ({ page }) => {
  await observe(page); let reads = 0; let release!: () => void
  const gate = new Promise<void>(done => { release = done })
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, sources: [source] } }))
  await page.route('**/api/v1/agent/sources/chunk?*', async r => {
    if (++reads === 1) return r.fulfill({ status: 503, json: { detail: '来源读取失败' } })
    if (pendingRetry) await gate
    await r.fulfill({ json: { ...source, text: '实际重新核对的片段' } }).catch(() => {})
  })
  await page.goto('/chat'); const button = page.getByRole('button', { name: /S1.*核对教材/ }); await button.click()
  await page.getByRole('button', { name: '重新读取来源' }).click()
  await expect(page.getByRole('dialog')).toContainText(pendingRetry ? '正在核对并读取来源' : '实际重新核对的片段')
  try { await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toBeHidden(); await expect(button).toBeFocused() }
  finally { release() }
})
for (const width of [1440, 375]) for (const theme of ['light', 'dark']) test(`saved inspection ${width} ${theme} is keyboard readable and fits viewport`, async ({ page }) => {
  await observe(page); await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
  await page.route('**/api/v1/agent/runs/inspection-run', r => r.fulfill({ json: { ...record, status: 'cancelled', steps: [...steps, { seq: 5, type: 'run_cancelled', at }] } }))
  await page.goto('/chat'); if (theme === 'dark') await page.getByRole('button', { name: '切换到深色模式' }).click()
  const summary = page.getByText('查看已保存步骤', { exact: true }); await summary.focus(); await page.keyboard.press('Enter')
  await expect(page.getByText('任务已取消', { exact: true })).toBeVisible()
  await expect(page.locator('.chat-agent-recovery')).toContainText('actual/model-for-inspection')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.locator('.chat-agent-inspection').scrollIntoViewIfNeeded()
  const out = resolve('../docs/open-source-integration/native-agent-shutdown-2026-10-11/screenshots'); await mkdir(out, { recursive: true })
  await page.screenshot({ path: resolve(out, `inspection-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  await page.locator('.chat-agent-steps > p').scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(out, `inspection-bottom-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  await summary.focus(); await page.keyboard.press('Enter'); await expect(page.getByText('任务已取消', { exact: true })).toBeHidden()
  await page.getByRole('button', { name: '关闭任务提示' }).focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('region', { name: '上次资料任务' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '查看任务记录' })).toBeFocused()
})
