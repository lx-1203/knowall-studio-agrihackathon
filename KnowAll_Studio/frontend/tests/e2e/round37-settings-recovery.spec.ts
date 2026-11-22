import { test, expect } from './fixtures'
const usage = (tokens: number) => ({ tokens, input_tokens: tokens, output_tokens: 0, calls: 0, unmetered_calls: 0, day: '2026-10-20', utc_offset_minutes: 480, scope: 'account', source: 'local_call_logs', checked_at: '2026-10-20T14:00:00Z' })
const cache = (active_entries: number) => ({ active_entries, stored_entries: active_entries, expired_entries: 0, invalid_entries: 0, scope: 'server', source: 'cache_database', checked_at: '2026-10-20T14:00:00Z' })
const resources = [
  { path: '/ai/usage', title: '用量信息加载失败', card: '今日系统记录', retry: '重新加载用量', json: usage(123) },
  { path: '/ai/cache/stats', title: '缓存信息加载失败', card: '服务端有效缓存', retry: '重新加载缓存信息', json: cache(7) },
  { path: '/mcp/servers', title: 'MCP 服务器加载失败', card: 'MCP 服务器', retry: '重新加载 MCP 服务器', json: { servers: [{ name: '我的测试工具', transport: 'stdio', command: 'local-only' }] } },
  { path: '/mcp/tools/builtin', title: '内置工具加载失败', card: '内置工具', retry: '重新加载内置工具', json: { tools: [{ name: '测试内置工具', description: '合成描述' }] } },
  { path: '/ai/calls', title: '调用日志加载失败', card: 'API 调用日志', retry: '重新加载调用日志', json: [{ id: 'only-log', model_name: '恢复模型', created_at: '2026-10-09T19:40:00Z', tokens_input: 0, tokens_output: 3, success: true, from_cache: true }] },
]
for (const resource of resources) test(resource.path + ' failure stays visible and has independent recovery', async ({ page }) => {
  let fail = true, reads = 0
  await page.route('**/api/v1' + resource.path + '**', route => {
    reads++
    return route.fulfill(fail ? { status: 403, json: { detail: 'PRIVATE_CONFIG_TOKEN_DO_NOT_RENDER' } } : { json: resource.json })
  })
  await page.goto('/settings')
  await expect(page.getByText(resource.title, { exact: true })).toBeVisible()
  await expect(page.getByText('PRIVATE_CONFIG_TOKEN_DO_NOT_RENDER')).toHaveCount(0)
  expect(reads).toBe(1) // 403 must not trigger the safe-GET retries.
  fail = false
  await page.getByRole('button', { name: resource.retry, exact: true }).click()
  await expect(page.getByText(resource.title, { exact: true })).toHaveCount(0)
  await expect.poll(() => reads).toBe(2)
  if (resource.path.includes('usage')) {
    await expect(page.getByText('123', { exact: true })).toBeVisible()
  } else if (resource.path.includes('stats')) await expect(page.getByText('7', { exact: true })).toBeVisible()
  else if (resource.path.includes('servers')) await expect(page.getByText('我的测试工具', { exact: true })).toBeVisible()
  else if (resource.path.includes('builtin')) await expect(page.getByText('测试内置工具', { exact: true })).toBeVisible()
  else await expect(page.getByText('恢复模型', { exact: true })).toBeVisible()
})

test('successful zero/empty differs from rejected reads and one failure keeps other resources usable', async ({ page }) => {
  await page.route('**/api/v1/ai/usage?*', r => r.fulfill({ json: usage(0) }))
  await page.route('**/api/v1/mcp/servers', r => r.fulfill({ json: { servers: [] } }))
  await page.route('**/api/v1/mcp/tools/builtin', r => r.fulfill({ status: 403, json: { detail: 'private' } }))
  await page.goto('/settings')
  await expect(page.getByText('内置工具加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('暂无外部 MCP 服务器', { exact: true })).toBeVisible()
  await expect(page.locator('.ant-card').filter({ has: page.getByText('今日系统记录', { exact: true }) }).locator('.ant-statistic-content-value')).toHaveText('0')
  await expect(page.getByText('用量信息加载失败', { exact: true })).toHaveCount(0)
})

test('pending quota shows loading instead of fabricated zero', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/ai/usage?*', async route => { await gate; await route.fulfill({ json: usage(0) }) })
  await page.goto('/settings')
  const card = page.locator('.ant-card').filter({ has: page.getByText('今日系统记录', { exact: true }) })
  await expect(card.locator('.ant-skeleton')).toBeVisible()
  await expect(card.locator('.ant-statistic-content-value')).toHaveCount(0)
  release()
  await expect(card.locator('.ant-statistic-content-value')).toHaveText('0')
})

test('leaving Settings cancels its pending native Axios read', async ({ page }) => {
  await page.addInitScript(() => {
    const urls = new WeakMap<XMLHttpRequest, string>()
    const open = XMLHttpRequest.prototype.open, abort = XMLHttpRequest.prototype.abort
    const observed: string[] = []
    Object.assign(window, { __nativeAbortedURLs: observed })
    XMLHttpRequest.prototype.open = function (method: string, url: string | URL, async = true, user?: string | null, password?: string | null) {
      urls.set(this, String(url)); return open.call(this, method, url, async, user, password)
    }
    XMLHttpRequest.prototype.abort = function () {
      observed.push(urls.get(this) || ''); return abort.call(this)
    }
  })
  let release!: () => void, requested = false
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/ai/usage?*', async route => { requested = true; await gate; await route.fulfill({ json: usage(999) }).catch(() => {}) })
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  // Establish that the read actually started before leaving; navigation can beat the effect.
  await expect.poll(() => requested).toBe(true)
  await page.getByRole('link', { name: '我的资料', exact: true }).click()
  await expect(page).not.toHaveURL(/settings/)
  // The spy observes and calls the real native abort; it does not mock transport.
  await expect.poll(() => page.evaluate(() => (window as Window & { __nativeAbortedURLs: string[] }).__nativeAbortedURLs.filter(url => url.includes('/ai/usage?')).length)).toBe(1)
  release()
})

test('pending log refresh cannot overlap and next refresh replaces old rows', async ({ page }) => {
  let reads = 0, release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/ai/calls?*', async route => {
    const n = ++reads
    if (n === 1) { await gate; await route.fulfill({ json: [{ id: 'old', model_name: '过期模型' }] }).catch(() => {}); return }
    await route.fulfill({ json: [{ id: 'new', model_name: '当前模型', created_at: '2026-10-09T19:40:00Z' }] })
  })
  await page.goto('/settings')
  const card = page.locator('.ant-card').filter({ has: page.getByText('API 调用日志', { exact: true }) })
  // Pending refresh is disabled to avoid overlapping reads from repeated user clicks.
  await expect(card.getByRole('button', { name: /刷\s*新/ })).toBeDisabled()
  release()
  await expect(page.getByText('过期模型', { exact: true })).toBeVisible()
  await card.getByRole('button', { name: /刷\s*新/ }).click()
  await expect(page.getByText('当前模型', { exact: true })).toBeVisible()
  await expect(page.getByText('过期模型', { exact: true })).toHaveCount(0)
})
