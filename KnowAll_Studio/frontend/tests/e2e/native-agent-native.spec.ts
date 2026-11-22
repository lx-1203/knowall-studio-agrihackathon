import { test, expect } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

test('authenticated native Agent browser to main API and SQL', async ({ page }) => {
  test.skip(!process.env.AGENT_NATIVE_BASE, 'Run through test_agent_chat_browser_native.py with real temporary JWT/SQL/provider')
  const network: { path: string; status: number; body: unknown }[] = []
  await page.addInitScript(token => {
    localStorage.setItem('knowall_token', token)
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'alice', username: 'alice', email: 'alice@test.invalid' }))
  }, process.env.AGENT_NATIVE_TOKEN!)
  // This is a local HTTP reverse proxy, not a response stub. Every Agent/chat
  // response below comes from current main.app, its JWT dependency and SQL.
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/v1/agent/') && !url.pathname.startsWith('/api/v1/chat/')) { await route.fulfill({ json: [] }); return }
    const response = await route.fetch({ url: process.env.AGENT_NATIVE_BASE + url.pathname + url.search })
    network.push({ path: url.pathname + url.search, status: response.status(), body: await response.json() })
    await route.fulfill({ response })
  })
  await page.routeWebSocket(/\/ws\//, () => {})
  await page.goto('/chat')
  await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await expect(page.getByRole('textbox', { name: '输入学习问题' })).toBeEnabled()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('请根据资料解释细胞呼吸')
  await page.getByRole('button', { name: /发送$/ }).click()
  await expect(page.locator('.chat-markdown')).toContainText('细胞呼吸消耗氧气 [S1]')
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索完成', { exact: true })).toBeVisible()
  await expect(page.getByText('来源读取完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /S1.*a.txt/ }).click()
  await expect(page.getByRole('dialog')).toContainText('细胞呼吸消耗氧气')
  await page.keyboard.press('Escape')
  await page.getByText('执行记录', { exact: true }).click()
  await page.locator('.chat-markdown').scrollIntoViewIfNeeded()
  const dir = resolve('../docs/open-source-integration/native-agent-chat-2026-10-11/native-browser')
  await mkdir(dir, { recursive: true })
  await page.screenshot({ path: resolve(dir, 'current-production-grounded-answer.png'), fullPage: true })
  for (const width of [1440, 375]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
    if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: theme === 'dark' ? '切换到深色模式' : '切换到浅色模式' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expect(page.locator('.chat-markdown')).toContainText('细胞呼吸消耗氧气 [S1]')
    await page.locator('.chat-markdown').scrollIntoViewIfNeeded()
    await expect(page.locator('.chat-markdown')).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: resolve(dir, `current-production-native-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  }
  await writeFile(resolve(dir, 'browser-http.json'), JSON.stringify({ scope: 'current production browser -> local HTTP reverse proxy -> real main.app/JWT/SQLite -> explicit simulated provider', paid_calls: 0, network }, null, 2))
  expect(network.filter(n => n.path === '/api/v1/agent/answer')).toHaveLength(1)
  expect(network.find(n => n.path.startsWith('/api/v1/agent/sources/'))?.status).toBe(200)
})
