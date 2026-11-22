import { test, expect } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

test('actual native task persists receipts and restores them after browser refresh', async ({ page }) => {
  test.skip(!process.env.AGENT_NATIVE_BASE, 'Run through test_agent_durable_browser_native.py with temporary JWT/SQL/local simulated provider')
  const traffic: { path: string; status: number; body: unknown }[] = []
  await page.addInitScript(token => {
    localStorage.setItem('knowall_token', token)
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'alice', username: 'alice', email: 'alice@test.invalid' }))
  }, process.env.AGENT_NATIVE_TOKEN!)
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/v1/agent/') && !url.pathname.startsWith('/api/v1/chat/')) { await route.fulfill({ json: [] }); return }
    const response = await route.fetch({ url: process.env.AGENT_NATIVE_BASE + url.pathname + url.search })
    traffic.push({ path: url.pathname + url.search, status: response.status(), body: await response.json() })
    await route.fulfill({ response })
  })
  await page.routeWebSocket(/\/ws\//, () => {})
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await expect(page.getByRole('textbox', { name: '输入学习问题' })).toBeEnabled()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('请根据资料解释细胞呼吸')
  await page.getByRole('button', { name: /发送$/ }).click()
  await expect(page.locator('.chat-markdown')).toContainText('细胞呼吸消耗氧气 [S1]')
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: /请根据资料解释细胞呼吸.*答疑助教/ }).click()
  await expect(page.locator('.chat-markdown')).toContainText('细胞呼吸消耗氧气 [S1]')
  await expect(page.getByText('资料 Agent · 只读', { exact: true })).toBeVisible()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('来源读取完成', { exact: true })).toBeVisible()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  await page.getByRole('button', { name: /S1.*a.txt/ }).click()
  await expect(page.getByRole('dialog', { name: '来源 S1' })).toContainText('细胞呼吸消耗氧气')
  await page.keyboard.press('Escape'); await page.getByText('执行记录', { exact: true }).click()
  const dir = resolve('../docs/open-source-integration/native-agent-durable-2026-10-11/native-browser')
  await mkdir(dir, { recursive: true })
  for (const width of [1440, 375]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
    if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: theme === 'dark' ? '切换到深色模式' : '切换到浅色模式' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.locator('.chat-markdown').scrollIntoViewIfNeeded(); await expect(page.locator('.chat-markdown')).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: resolve(dir, `native-restored-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  }
  expect(traffic.filter(t => t.path === '/api/v1/agent/answer')).toHaveLength(1)
  const restored = traffic.find(t => t.path.endsWith('/receipts'))
  expect(restored?.status).toBe(200)
  const saved = restored?.body as { receipts: { run_id: string; assistant_message_id: string; sources: { text: string }[] }[] }
  expect(saved.receipts).toHaveLength(1); expect(saved.receipts[0].sources[0].text).toBe('')
  expect(saved.receipts[0].run_id).toBeTruthy(); expect(saved.receipts[0].assistant_message_id).toBeTruthy()
  await writeFile(resolve(dir, 'browser-http.json'), JSON.stringify({ scope: 'current dist -> route.fetch local proxy -> actual main.app/JWT/temp SQLite; provider explicitly local simulated', paid_calls: 0, traffic }, null, 2))
})
