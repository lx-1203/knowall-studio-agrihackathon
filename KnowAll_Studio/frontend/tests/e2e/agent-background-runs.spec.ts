import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
const source = { citation_id: 'S1', document_id: 'doc', chunk_id: 'chunk', filename: '后台教材.pdf', page_range: '3', text: '', chunk_sha256: 'a'.repeat(64), truncated: false }
const steps = [{ seq: 1, type: 'run_started', at: '2026-10-11T14:40:00Z' }, { seq: 2, type: 'step_started', at: '2026-10-11T14:40:01Z' }]
const running = { run_id: 'background-run', conversation_id: null, assistant_message_id: null, status: 'running', scope: 'readonly_documents', sources: [], steps, model: 'local/model', error_code: null, recovery_state: 'active', execution_mode: 'background', cancel_requested: false, created_at: '2026-10-11T14:40:00Z' }
const completed = { ...running, conversation_id: 'background-conv', assistant_message_id: 'background-message', status: 'completed', recovery_state: 'terminal', sources: [source], steps: [...steps, { seq: 3, type: 'run_completed', at: '2026-10-11T14:40:02Z' }] }
async function setup(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/agent/models', route => route.fulfill({ json: { streaming_events: true, background_runs: true, models: [{ value: 'local/model', label: '本地验证模型', model_tool_support: 'unverified' }] } }))
}
async function begin(page: import('@playwright/test').Page) {
  await setup(page); await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('switch', { name: '离开页面后继续' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('后台解释呼吸')
  await page.getByRole('button', { name: /发送$/ }).click()
}
test('background observation can leave the page without sending a cancel or duplicate POST', async ({ page }) => {
  let starts = 0; let cancels = 0
  await page.route('**/api/v1/agent/runs', route => { starts++; return route.fulfill({ status: 202, json: { run_id: 'background-run', execution_mode: 'background' } }) })
  await page.route('**/api/v1/agent/runs/background-run', route => route.fulfill({ json: running }))
  await page.route('**/api/v1/agent/runs/background-run/cancel', route => { cancels++; return route.fulfill({ status: 202, json: { run_id: 'background-run', status: 'running', cancel_requested: true } }) })
  await begin(page); await expect(page.getByText('正在执行 · 请求模型判断下一步', { exact: true })).toBeVisible()
  await page.reload(); await expect(page.getByText('上次资料任务的状态需要核对', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '继续查看进度' })).toBeVisible()
  expect(starts).toBe(1); expect(cancels).toBe(0)
})
for (const cancelCode of ['REQUEST_CANCELLED', 'BACKGROUND_CANCELLED']) test(`explicit stop ${cancelCode} saves request first, failure permits retry, and only real cancelled status ends task`, async ({ page }) => {
  let cancels = 0; let stopping = false; let ended = false
  await page.route('**/api/v1/agent/runs', route => route.fulfill({ status: 202, json: { run_id: 'background-run', execution_mode: 'background' } }))
  await page.route('**/api/v1/agent/runs/background-run', route => route.fulfill({ json: ended ? { ...running, cancel_requested: true, status: 'cancelled', recovery_state: 'terminal', error_code: cancelCode, steps: [...steps, { seq: 3, type: 'run_cancelled', at: '2026-10-11T14:40:02Z' }] } : { ...running, cancel_requested: stopping } }))
  await page.route('**/api/v1/agent/runs/background-run/cancel', route => { cancels++; if (cancels === 1) return route.fulfill({ status: 503, json: { detail: '停止请求暂时无法保存' } }); stopping = true; return route.fulfill({ status: 202, json: { run_id: 'background-run', status: 'running', cancel_requested: true } }) })
  await begin(page); await expect(page.getByText('正在执行 · 请求模型判断下一步', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '停止后台任务' }).focus(); await page.keyboard.press('Enter')
  await expect(page.getByText('停止请求暂时无法保存', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '停止后台任务' })).toBeEnabled()
  await page.getByRole('button', { name: '停止后台任务' }).click()
  await expect(page.getByText('停止请求已保存，等待任务结束', { exact: true })).toBeVisible()
  await expect(page.getByText('已停止资料任务', { exact: true })).toHaveCount(0)
  ended = true
  await expect(page.getByText('资料任务已取消。', { exact: true }).first()).toBeVisible()
  expect(cancels).toBe(2)
})
test('saved background result is read once and never generated through old answer endpoint', async ({ page }) => {
  let reads = 0; let legacy = 0
  await page.route('**/api/v1/agent/runs', route => route.fulfill({ status: 202, json: { run_id: 'background-run', execution_mode: 'background' } }))
  await page.route('**/api/v1/agent/runs/background-run', route => route.fulfill({ json: completed }))
  await page.route('**/api/v1/agent/runs/background-run/result', route => { reads++; return route.fulfill({ json: { ...completed, message: '后台已保存回答[S1]' } }) })
  await page.route('**/api/v1/agent/answer**', route => { legacy++; return route.fulfill({ status: 500 }) })
  await begin(page); await expect(page.locator('.chat-markdown')).toContainText('后台已保存回答[S1]')
  expect(reads).toBe(1); expect(legacy).toBe(0)
})
test('uncertain background submission does not automatically POST again', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/agent/runs', route => { calls++; return route.abort('failed') })
  await begin(page)
  await expect(page.getByText(/任务提交未确认，请先查看任务记录/).first()).toBeVisible()
  expect(calls).toBe(1)
})
test('run list discovers tasks without local pointer, paginates and opens actual conversation', async ({ page }) => {
  const offsets: string[] = []
  await page.route('**/api/v1/agent/runs?*', route => {
    const offset = new URL(route.request().url()).searchParams.get('offset')!; offsets.push(offset)
    return route.fulfill({ json: { runs: offset === '0' ? [running] : [completed], has_more: offset === '0' } })
  })
  await page.route('**/api/v1/chat/conversations/background-conv', route => route.fulfill({ json: { conversation_id: 'background-conv', title: '保存的后台对话', role_preset: 'tutor', messages: [{ id: 'background-message', role: 'assistant', content: '保存的后台回答' }] } }))
  await page.goto('/chat'); await page.getByRole('button', { name: '查看任务记录' }).click()
  const dialog = page.getByRole('dialog', { name: '资料任务记录' })
  await expect(dialog.getByText('运行中', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '下一页' }).click()
  await expect(dialog.getByText('已完成', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '查看已保存对话' }).click()
  await expect(page.locator('.chat-markdown')).toContainText('保存的后台回答')
  expect(offsets).toEqual(['0', '20'])
})
test('run list failed read retries independently and malformed completion is rejected', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/agent/runs?*', route => route.fulfill(++calls === 1 ? { status: 503, json: { detail: '任务记录暂时无法读取' } } : calls === 2 ? { json: { runs: [{ ...completed, assistant_message_id: null }], has_more: false } } : { json: { runs: [], has_more: false } }))
  await page.goto('/chat'); await page.getByRole('button', { name: '查看任务记录' }).click()
  await expect(page.getByText('任务记录暂时无法读取', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新读取任务记录' }).click()
  await expect(page.getByText('任务清单格式异常，请重新读取。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新读取任务记录' }).click()
  await expect(page.getByText('暂无资料任务记录', { exact: true })).toBeVisible()
})
test('resuming another task loads its real conversation and actual model without mixing old messages', async ({ page }) => {
  let finish = false
  await page.route('**/api/v1/chat/conversations', route => route.fulfill({ json: [{ id: 'old-conv', title: '旧会话A', role_preset: 'tutor', created_at: '2026-10-11T14:00:00Z' }] }))
  await page.route('**/api/v1/chat/conversations/old-conv', route => route.fulfill({ json: { conversation_id: 'old-conv', title: '旧会话A', role_preset: 'tutor', messages: [{ id: 'old-message', role: 'assistant', content: '旧会话独有内容A' }] } }))
  await page.route('**/api/v1/agent/runs?*', route => route.fulfill({ json: { runs: [running], has_more: false } }))
  await page.route('**/api/v1/agent/runs/background-run', route => route.fulfill({ json: { ...(finish ? completed : running), model: 'local/model-B' } }))
  await page.route('**/api/v1/agent/runs/background-run/result', route => route.fulfill({ json: { ...completed, model: 'local/model-B', message: '后台实际内容B[S1]' } }))
  await page.route('**/api/v1/chat/conversations/background-conv', route => route.fulfill({ json: { conversation_id: 'background-conv', title: '后台会话B', role_preset: 'tutor', messages: [{ id: 'background-message', role: 'assistant', content: '后台实际内容B[S1]' }] } }))
  await page.goto('/chat'); await page.getByRole('button', { name: /旧会话A/ }).click()
  await expect(page.locator('.chat-markdown')).toContainText('旧会话独有内容A')
  await page.getByRole('button', { name: '查看任务记录' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '核对运行状态' }).click()
  await page.getByRole('button', { name: '继续查看进度' }).click()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('运行中 · 实时事件已保存 · local/model-B', { exact: true })).toBeVisible()
  finish = true
  await expect(page.locator('.chat-markdown').filter({ hasText: '后台实际内容B' })).toBeVisible()
  await expect(page.locator('.chat-markdown').filter({ hasText: '旧会话独有内容A' })).toHaveCount(0)
  await expect(page.locator('.chat-header h1')).toHaveText('后台会话B')
})
for (const width of [1440, 375]) for (const theme of ['light', 'dark']) test(`run list ${width} ${theme} has keyboard control and fits viewport`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
  await page.route('**/api/v1/agent/runs?*', route => route.fulfill({ json: { runs: [running, { ...completed, run_id: 'completed-list-run' }], has_more: false } }))
  await page.goto('/chat')
  if (theme === 'dark') await page.getByRole('button', { name: '切换到深色模式' }).click()
  await page.getByRole('button', { name: '查看任务记录' }).focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog', { name: '资料任务记录' })).toBeVisible()
  await expect(page.getByRole('dialog').getByText('运行中', { exact: true })).toBeVisible()
  await expect(page.getByRole('dialog').getByText('已完成', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  const out = resolve('../docs/open-source-integration/native-agent-background-2026-10-11/screenshots'); await mkdir(out, { recursive: true })
  await page.screenshot({ path: resolve(out, `list-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  await page.keyboard.press('Escape'); await expect(page.getByRole('button', { name: '查看任务记录' })).toBeFocused()
})
