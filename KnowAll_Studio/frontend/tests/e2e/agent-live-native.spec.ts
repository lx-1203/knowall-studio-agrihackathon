import { test, expect } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const out = resolve('../docs/open-source-integration/native-agent-events-2026-10-11/native-browser')
test.beforeEach(async ({ page }) => {
  test.skip(!process.env.AGENT_NATIVE_CONTROL, 'Run via temporary SQL/native provider harness')
  await page.addInitScript(token => {
    localStorage.setItem('knowall_token', token)
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'alice', username: 'alice', email: 'alice@test.invalid' }))
  }, process.env.AGENT_NATIVE_TOKEN!)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.startsWith('/api/v1/agent/') || path.startsWith('/api/v1/chat/')) await route.fallback()
    else await route.fulfill({ json: [] })
  })
  await page.routeWebSocket(/\/ws\//, () => {})
  await mkdir(out, { recursive: true })
})

async function begin(page: import('@playwright/test').Page, question: string) {
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill(question)
  await page.getByRole('button', { name: /发送$/ }).click()
  await expect(page.getByRole('status').filter({ hasText: '正在执行 · 请求模型判断下一步' })).toBeVisible()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索完成', { exact: true })).toBeVisible()
  await expect(page.locator('.chat-agent-live')).toContainText('请求模型判断下一步')
  const runId = await page.evaluate(() => JSON.parse(localStorage.getItem('knowall_agent_observed_run:alice')!).run_id as string)
  return runId
}

test('actual streaming events commit before final answer and survive history refresh', async ({ page, request }) => {
  const runId = await begin(page, '实时验证细胞呼吸')
  const live = await (await request.get(process.env.AGENT_NATIVE_CONTROL! + '/state')).json()
  const running = live.runs.find((row: { id: string }) => row.id === runId)
  expect(running.status).toBe('running'); expect(running.steps.at(-1).type).toBe('step_started')
  expect(live.message_count).toBe(0); expect(running.steps.some((step: { type: string }) => step.type === 'run_completed')).toBe(false)
  for (const width of [1440, 375]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
    if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: theme === 'dark' ? '切换到深色模式' : '切换到浅色模式' }).click()
    await expect(page.getByRole('status').filter({ hasText: '正在执行 ·' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: resolve(out, `actual-live-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  }
  await request.post(process.env.AGENT_NATIVE_CONTROL! + '/release')
  await expect(page.locator('.chat-markdown')).toContainText('细胞呼吸消耗氧气 [S1]')
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.reload()
  await page.getByRole('button', { name: /实时验证细胞呼吸.*答疑助教/ }).click()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('依据资料回答完成', { exact: true })).toBeVisible()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  await writeFile(resolve(out, 'live-before-final.json'), JSON.stringify({ scope: 'actual production browser -> Vite streaming proxy -> main/JWT/SQL -> native adapter -> explicit local gated provider; no Agent/chat API mocks', paid_calls: 0, run_id: runId, live }, null, 2))
})

test('actual keyboard stop persists cancellation and reload observes it without regeneration', async ({ page, request }) => {
  await request.post(process.env.AGENT_NATIVE_CONTROL! + '/reset')
  const runId = await begin(page, '停止验证细胞呼吸')
  await page.getByRole('button', { name: '停止回复' }).focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('textbox', { name: '输入学习问题' })).toBeFocused()
  await expect(page.getByText('已停止资料任务', { exact: true })).toBeVisible()
  await expect.poll(async () => {
    const response = await request.get(process.env.AGENT_NATIVE_CONTROL! + '/state')
    return (await response.json()).runs.find((row: { id: string }) => row.id === runId)?.status
  }).toBe('cancelled')
  await request.post(process.env.AGENT_NATIVE_CONTROL! + '/release')
  await page.reload(); await expect(page.getByText('上次资料任务已取消', { exact: true })).toBeVisible()
  const after = await (await request.get(process.env.AGENT_NATIVE_CONTROL! + '/state')).json()
  expect(after.message_count).toBe(2)
  const cancelled = after.runs.find((row: { id: string }) => row.id === runId)
  expect(cancelled.steps.at(-1).type).toBe('run_cancelled')
  expect(cancelled.steps.some((step: { type: string }) => step.type === 'tool_completed')).toBe(true)
  await page.screenshot({ path: resolve(out, 'actual-reload-cancelled.png'), fullPage: true, animations: 'disabled' })
  await writeFile(resolve(out, 'actual-cancel.json'), JSON.stringify({ run_id: runId, paid_calls: 0, after }, null, 2))
})
