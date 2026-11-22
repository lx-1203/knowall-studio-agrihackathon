import { test, expect } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

test('isolated real login and database restore the saved Agent execution', async ({ page }) => {
  // Actual app, authentication, SQL and local provider: no route interception.
  const statuses: { path: string; status: number }[] = []
  page.on('response', response => {
    const path = new URL(response.url()).pathname
    if (path.startsWith('/api/')) statuses.push({ path, status: response.status() })
  })
  await page.goto('/chat')
  await page.getByLabel('账号', { exact: true }).fill('agent_preview')
  await page.getByLabel('密码', { exact: true }).fill('AgentPreview2026!')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('switch', { name: '只读资料 Agent' })).toBeVisible()
  await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('switch', { name: '离开页面后继续' }).click()
  await expect(page.getByRole('textbox', { name: '输入学习问题' })).toBeEnabled()
  const answerResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/agent/runs' && response.request().method() === 'POST')
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('后台隔离验证：细胞呼吸是什么？')
  await page.getByRole('button', { name: /发送$/ }).click()
  expect((await answerResponse).status()).toBe(202)
  await expect(page.locator('.chat-markdown')).toContainText('本地模拟回答，仅验证流程')
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  // Chromium does not expose EventSource response bodies through CDP.
  // Read the actual stored receipt using this browser's real authenticated API.
  const answer = await page.evaluate(async () => {
    const headers = { Authorization: 'Bearer ' + localStorage.getItem('knowall_token') }
    const conversations = await (await fetch('/api/v1/chat/conversations', { headers })).json()
    const id = conversations[0].id
    const history = await (await fetch(`/api/v1/agent/conversations/${id}/receipts`, { headers })).json()
    return { ...history.receipts[0], conversation_id: id }
  })
  expect(answer.run_id).toBeTruthy()
  expect(answer.steps.at(-1).type).toBe('run_completed')
  await page.reload()
  await page.getByRole('button', { name: /后台隔离验证：细胞呼吸是什么.*答疑助教/ }).first().click()
  await expect(page.locator('.chat-markdown')).toContainText('本地模拟回答，仅验证流程')
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText(/已保存的执行记录/)).toBeVisible()
  await page.getByRole('button', { name: /S1.*本地验证样例/ }).click()
  await expect(page.getByRole('dialog', { name: '来源 S1' })).toContainText('此文本为本地验证样例')
  await page.keyboard.press('Escape')
  await page.getByText('执行记录', { exact: true }).click()
  const out = resolve('../docs/open-source-integration/native-agent-background-2026-10-11/isolated-preview')
  await mkdir(out, { recursive: true })
  for (const width of [1440, 375]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
    if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: theme === 'dark' ? '切换到深色模式' : '切换到浅色模式' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.locator('.chat-markdown').scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: resolve(out, `real-app-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  }
  expect(statuses.filter(response => response.path === '/api/v1/agent/runs')).toHaveLength(1)
  await writeFile(resolve(out, 'browser-verification.json'), JSON.stringify({ at: new Date().toISOString(), scope: 'actual login/full app/isolated SQLite; explicit local simulated model; no browser API mocks', paid_calls: 0, run_id: answer.run_id, conversation_id: answer.conversation_id, statuses }, null, 2))
})
