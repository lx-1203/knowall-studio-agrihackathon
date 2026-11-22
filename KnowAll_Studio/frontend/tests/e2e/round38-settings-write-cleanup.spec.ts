import { test, expect } from './fixtures'
for (const mutation of ['cache', 'mcp']) test('late ' + mutation + ' completion after leaving never starts orphan reads, re-entry loads normally', async ({ page }) => {
  let reads = 0, writes = 0, requested = false, release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const readPath = mutation === 'cache' ? '/ai/cache/stats' : '/mcp/servers'
  const writePath = mutation === 'cache' ? '/admin/cache/clear' : '/mcp/servers'
  await page.route('**/api/v1' + readPath, async r => {
    if (r.request().method() === 'POST') {
      writes++; requested = true; await gate; await r.fulfill({ json: { ok: true } }); return
    }
    reads++; await r.fulfill({ json: mutation === 'cache' ? { active_entries: 9, stored_entries: 9, expired_entries: 0, invalid_entries: 0, scope: 'server', source: 'cache_database', checked_at: '2026-10-20T14:00:00Z' } : { servers: [] } })
  })
  if (mutation === 'cache') await page.route('**/api/v1' + writePath, async r => { writes++; requested = true; await gate; await r.fulfill({ json: { ok: true } }) })
  await page.goto('/settings')
  await expect.poll(() => reads).toBe(1)
  if (mutation === 'cache') await page.getByRole('button', { name: /清除缓存/ }).click()
  else {
    await page.getByRole('button', { name: /添加 MCP 服务器/ }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByPlaceholder('例如: my-tools').fill('synthetic-only')
    await dialog.getByPlaceholder(/npx/).fill('never-execute')
    await dialog.getByRole('button', { name: /添\s*加/ }).click()
  }
  await expect.poll(() => requested).toBe(true)
  if (mutation === 'mcp') await page.getByRole('dialog').getByRole('button', { name: /取\s*消/ }).click()
  await page.getByRole('link', { name: '我的资料', exact: true }).click()
  await expect(page).not.toHaveURL(/settings/)
  // URL changes can precede Suspense committing the new lazy page; prove unmount first.
  await expect(page.getByRole('button', { name: '刷新统计与余额', exact: true })).toHaveCount(0)
  // Response observation + browser task flush ensures write callback has run.
  const completed = page.waitForResponse(r => r.url().endsWith(writePath) && r.request().method() !== 'GET')
  release(); await completed
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  expect(writes).toBe(1); expect(reads).toBe(1)
  await page.getByRole('link', { name: '系统设置', exact: true }).click()
  await expect.poll(() => reads).toBe(2)
})
