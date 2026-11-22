import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const source = { citation_id: 'S1', document_id: 'doc', chunk_id: 'chunk', filename: '实时教材.pdf', page_range: '7', text: '细胞呼吸产生ATP。', chunk_sha256: 'a'.repeat(64), truncated: false }
const steps = [
  { seq: 1, type: 'run_started', at: '2026-10-11T14:00:00Z' },
  { seq: 2, type: 'step_started', at: '2026-10-11T14:00:01Z' },
  { seq: 3, type: 'tool_started', tool: 'search_documents', at: '2026-10-11T14:00:02Z' },
  { seq: 4, type: 'tool_completed', tool: 'search_documents', at: '2026-10-11T14:00:03Z' },
  { seq: 5, type: 'run_completed', at: '2026-10-11T14:00:04Z' },
]
const result = { status: 'completed', scope: 'readonly_documents', message: '呼吸产生ATP。[S1]', conversation_id: 'live-conv', run_id: 'live-run', assistant_message_id: 'live-message', sources: [source], steps, model: 'local/model' }
type StreamControl = { push: (data: unknown) => void; end: () => void; calls: number; ordinaryCalls: number }
declare global { interface Window { __agentTestStream: StreamControl } }

async function stream(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/agent/models', route => route.fulfill({ json: { streaming_events: true, models: [{ value: 'local/model', label: '本地验证模型', model_tool_support: 'unverified' }] } }))
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    let writer: ReadableStreamDefaultController<Uint8Array> | null = null
    window.__agentTestStream = {
      calls: 0, ordinaryCalls: 0,
      push(data) { writer!.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(data) + '\n\n')) },
      end() { writer!.close() },
    }
    window.fetch = (input, init) => {
      const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname
      if (path === '/api/v1/agent/answer') window.__agentTestStream.ordinaryCalls++
      if (path !== '/api/v1/agent/answer/stream') return original(input, init)
      window.__agentTestStream.calls++
      return Promise.resolve(new Response(new ReadableStream<Uint8Array>({ start(controller) { writer = controller } }), { headers: { 'Content-Type': 'text/event-stream' } }))
    }
  })
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('用资料解释呼吸')
  await page.getByRole('button', { name: /发送$/ }).click()
  await expect.poll(() => page.evaluate(() => window.__agentTestStream.calls)).toBe(1)
}
async function push(page: import('@playwright/test').Page, event: unknown) { await page.evaluate(data => window.__agentTestStream.push(data), event) }
async function start(page: import('@playwright/test').Page) {
  for (const event of steps.slice(0, 3)) await push(page, { kind: 'step', run_id: 'live-run', event })
}
async function complete(page: import('@playwright/test').Page) {
  for (const event of steps.slice(3)) await push(page, { kind: 'step', run_id: 'live-run', event })
  await push(page, { kind: 'result', run_id: 'live-run', result })
}

test('live saved steps appear before the final answer and completion is not faked', async ({ page }) => {
  await stream(page); await start(page)
  await expect(page.getByRole('status').filter({ hasText: '正在执行 · 资料检索开始' })).toBeVisible()
  await expect(page.getByRole('button', { name: '停止回复' })).toBeVisible()
  await expect(page.getByText('依据资料回答完成', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('knowall_agent_observed_run:e2e-user')!).run_id)).toBe('live-run')
  await complete(page)
  await expect(page.locator('.chat-markdown')).toContainText('呼吸产生ATP。[S1]')
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('knowall_agent_observed_run:e2e-user'))).toBeNull()
})

for (const failure of ['EOF', 'duplicate', 'wrong-run', 'error', 'changed-error', 'completed-error', 'invalid-time'] as const) test(`stream ${failure} keeps true progress and never starts a second answer`, async ({ page }) => {
  await stream(page); await start(page)
  if (failure === 'EOF') await page.evaluate(() => window.__agentTestStream.end())
  else if (failure === 'duplicate') await push(page, { kind: 'step', run_id: 'live-run', event: steps[2] })
  else if (failure === 'wrong-run') await push(page, { kind: 'step', run_id: 'foreign-run', event: steps[3] })
  else if (failure === 'changed-error') await push(page, { kind: 'error', run_id: 'live-run', code: 'RUN_TIMEOUT', steps: [steps[0], steps[1], { ...steps[2], type: 'tool_completed' }] })
  else if (failure === 'completed-error') await push(page, { kind: 'error', run_id: 'live-run', code: 'RUN_TIMEOUT', steps: [steps[0], steps[1], steps[2], { ...steps[3], type: 'run_completed' }] })
  else if (failure === 'invalid-time') await push(page, { kind: 'step', run_id: 'live-run', event: { ...steps[3], at: 'October 11, 2026 14:00:00 +00:00' } })
  else await push(page, { kind: 'error', run_id: 'live-run', code: 'RUN_TIMEOUT', steps: steps.slice(0, 3) })
  await expect(page.getByRole('button', { name: /发送$/ })).toBeVisible()
  await expect(page.locator('.chat-message-content .ant-alert-error')).toBeVisible()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索开始', { exact: true })).toBeVisible()
  await expect(page.getByText('依据资料回答完成', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => [window.__agentTestStream.calls, window.__agentTestStream.ordinaryCalls])).toEqual([1, 0])
})

test('keyboard stop keeps observed steps and returns focus without claiming saved completion', async ({ page }) => {
  await stream(page); await start(page)
  await page.getByRole('button', { name: '停止回复' }).focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('textbox', { name: '输入学习问题' })).toBeFocused()
  await expect(page.getByText('已停止资料任务', { exact: true })).toBeVisible()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索开始', { exact: true })).toBeVisible()
  await expect(page.getByText('依据资料回答完成', { exact: true })).toHaveCount(0)
})

test('an incomplete error receipt cannot erase observed steps or leave live progress running', async ({ page }) => {
  await stream(page); await start(page)
  await expect(page.getByRole('status').filter({ hasText: '正在执行 · 资料检索开始' })).toBeVisible()
  await push(page, { kind: 'error', run_id: 'live-run', code: 'RUN_TIMEOUT', steps: [] })
  await expect(page.locator('.chat-message-content .ant-alert-error')).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: '正在执行 ·' })).toHaveCount(0)
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索开始', { exact: true })).toBeVisible()
})

test('a final answer without its committed completion event is rejected', async ({ page }) => {
  await stream(page); await start(page)
  await push(page, { kind: 'result', run_id: 'live-run', result })
  await expect(page.locator('.chat-message-content .ant-alert-error')).toBeVisible()
  await expect(page.locator('.chat-markdown')).toHaveCount(0)
  expect(await page.evaluate(() => window.__agentTestStream.ordinaryCalls)).toBe(0)
})

async function observe(page: import('@playwright/test').Page) {
  await page.addInitScript(() => localStorage.setItem('knowall_agent_observed_run:e2e-user', JSON.stringify({ run_id: 'observed-run' })))
}
const run = { run_id: 'observed-run', conversation_id: 'stored-conv', assistant_message_id: 'stored-message', status: 'completed', scope: 'readonly_documents', sources: [source], steps, model: 'local/model', error_code: null, recovery_state: 'terminal' }

for (const malformed of ['incomplete-completion', 'array-status']) test(`observer rejects ${malformed} without claiming completion`, async ({ page }) => {
  await observe(page)
  const record = malformed === 'array-status' ? { ...run, status: ['completed'] }
    : { ...run, conversation_id: null, assistant_message_id: null, sources: [], steps: steps.slice(0, 3) }
  await page.route('**/api/v1/agent/runs/observed-run', route => route.fulfill({ json: record }))
  await page.goto('/chat')
  await expect(page.getByText('运行记录格式异常，请重新读取。', { exact: true })).toBeVisible()
  await expect(page.getByText('上次资料任务已完成', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '查看已保存对话' })).toHaveCount(0)
})

test('reload observer only reads the saved run and opens its actual conversation', async ({ page }) => {
  await observe(page)
  await page.route('**/api/v1/agent/runs/observed-run', route => route.fulfill({ json: run }))
  await page.route('**/api/v1/chat/conversations/stored-conv', route => route.fulfill({ json: { conversation_id: 'stored-conv', title: '已保存任务', role_preset: 'tutor', messages: [{ id: 'stored-message', role: 'assistant', content: '保存的回答' }] } }))
  await page.goto('/chat')
  await expect(page.getByText('上次资料任务已完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看已保存对话' }).click()
  await expect(page.locator('.chat-markdown')).toContainText('保存的回答')
})

test('unconfirmed running state is read-only and failed observation can retry', async ({ page }) => {
  await observe(page); let reads = 0
  await page.route('**/api/v1/agent/runs/observed-run', route => route.fulfill(++reads === 1 ? { status: 503, json: { detail: '暂时无法读取运行状态' } } : { json: { ...run, status: 'running', recovery_state: 'unconfirmed', sources: [], assistant_message_id: null } }))
  await page.goto('/chat'); await expect(page.getByText('暂时无法读取运行状态', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新读取运行状态' }).click()
  await expect(page.getByText('上次资料任务的状态需要核对', { exact: true })).toBeVisible()
  await expect(page.getByText(/没有自动继续执行或再次生成/)).toBeVisible()
  expect(reads).toBe(2)
})

test('another account observation is ignored and missing own run can be dismissed', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('knowall_agent_observed_run:other-user', JSON.stringify({ run_id: 'foreign-run' }))
    localStorage.setItem('knowall_agent_observed_run:e2e-user', JSON.stringify({ run_id: 'missing-run' }))
  })
  const requests: string[] = []
  await page.route('**/api/v1/agent/runs/*', route => { requests.push(new URL(route.request().url()).pathname); return route.fulfill({ status: 404, json: { detail: '运行记录不存在或当前账号无权访问' } }) })
  await page.goto('/chat'); await expect(page.getByText('运行记录不存在或当前账号无权访问', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭任务提示' }).click()
  expect(requests).toEqual(['/api/v1/agent/runs/missing-run'])
  expect(await page.evaluate(() => localStorage.getItem('knowall_agent_observed_run:e2e-user'))).toBeNull()
  expect(await page.evaluate(() => localStorage.getItem('knowall_agent_observed_run:other-user'))).toBeTruthy()
})

test('a pending observation can be dismissed without blocking ordinary chat', async ({ page }) => {
  await observe(page); let release!: () => void
  await page.route('**/api/v1/chat/roles', route => route.fulfill({ json: { tutor: { name: '学习辅导' } } }))
  const ready = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/agent/runs/observed-run', async route => { await ready; await route.fulfill({ json: run }) })
  try {
    await page.goto('/chat'); await expect(page.getByText('正在核对上次资料任务…', { exact: true })).toBeVisible()
    await expect(page.getByRole('textbox', { name: '输入学习问题' })).toBeEnabled()
    await expect(page.getByRole('button', { name: '关闭任务提示' })).toBeEnabled()
    await page.getByRole('button', { name: '关闭任务提示' }).click()
    await expect(page.getByRole('region', { name: '上次资料任务' })).toHaveCount(0)
  } finally { release() }
})

test('dismissing an older observation preserves a newer task pointer', async ({ page }) => {
  await observe(page)
  await page.route('**/api/v1/agent/runs/observed-run', route => route.fulfill({ json: run }))
  await page.goto('/chat'); await expect(page.getByText('上次资料任务已完成', { exact: true })).toBeVisible()
  await page.evaluate(() => localStorage.setItem('knowall_agent_observed_run:e2e-user', JSON.stringify({ run_id: 'newer-run' })))
  await page.getByRole('button', { name: '关闭任务提示' }).click()
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('knowall_agent_observed_run:e2e-user')!).run_id)).toBe('newer-run')
})

for (const width of [1440, 375]) for (const theme of ['light', 'dark']) test(`live progress ${width} ${theme} is keyboard reachable and fits the viewport`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
  await stream(page); await start(page)
  if (theme === 'dark') await page.getByRole('button', { name: '切换到深色模式' }).click()
  await expect(page.getByRole('status').filter({ hasText: '正在执行 · 资料检索开始' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.getByText('执行记录', { exact: true }).focus(); await page.keyboard.press('Enter')
  await expect(page.getByText('资料检索开始', { exact: true })).toBeVisible()
  const out = resolve('../docs/open-source-integration/native-agent-events-2026-10-11/screenshots'); await mkdir(out, { recursive: true })
  await page.screenshot({ path: resolve(out, `live-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  await complete(page); await expect(page.locator('.chat-markdown')).toContainText('呼吸产生ATP。[S1]')
})
