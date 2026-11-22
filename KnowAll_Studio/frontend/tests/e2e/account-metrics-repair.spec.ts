import { test, expect } from './fixtures'

const usage = (tokens = 60) => ({ tokens, input_tokens: tokens, output_tokens: 0, calls: 2, unmetered_calls: 0,
  day: '2026-10-20', utc_offset_minutes: 480, scope: 'account', source: 'local_call_logs', checked_at: '2026-10-20T14:00:00Z' })
const cache = (count = 3) => ({ active_entries: count, stored_entries: count + 2, expired_entries: 2, invalid_entries: 0,
  scope: 'server', source: 'cache_database', checked_at: '2026-10-20T14:00:00Z' })
const balance = (amount = '17.1200') => ({ status: 'available', is_available: true, connection_name: '我的 DeepSeek',
  provider: 'deepseek', source: 'provider_api', balances: [{ currency: 'CNY', total_balance: amount }], checked_at: '2026-10-20T14:00:00Z' })
async function setup(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/ai/config', r => r.fulfill({ json: { connections: [], default_model: '', presets: [], legacy_keys: [], server_connections: [] } }))
  await page.route('**/api/v1/admin/quota/status', r => r.fulfill({ json: { used_today: 152289, remaining: 847711 } }))
  await page.route('**/api/v1/admin/cache/stats', r => r.fulfill({ json: { total_entries: 11 } }))
  await page.route('**/api/v1/ai/usage?*', r => r.fulfill({ json: usage() }))
  await page.route('**/api/v1/ai/balance', r => r.fulfill({ json: balance() }))
  await page.route('**/api/v1/ai/cache/stats', r => r.fulfill({ json: cache() }))
}

test('metrics use their real sources and never display local allowance as provider balance', async ({ page }) => {
  await setup(page)
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  const stats = page.getByLabel('真实来源统计')
  await expect(stats).toContainText('今日系统记录')
  await expect(stats).toContainText('60')
  await expect(stats).toContainText('17.1200')
  await expect(stats).toContainText('CNY')
  await expect(stats).toContainText('服务端有效缓存')
  await expect(stats).toContainText('已保存 5 条')
  await expect(stats).toContainText('过期 2 条')
  await expect(stats).toContainText('全部账号')
  await expect(page.getByText('剩余额度', { exact: true })).toHaveCount(0)
  await expect(page.getByText('847,711', { exact: true })).toHaveCount(0)
})

test('missing statistics and unsupported provider show unknown instead of invented zero', async ({ page }) => {
  await setup(page)
  await page.route('**/api/v1/ai/usage?*', r => r.fulfill({ json: {} }))
  await page.route('**/api/v1/ai/balance', r => r.fulfill({ json: { status: 'unsupported', balances: [], message: '此服务未提供可用余额接口' } }))
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  const stats = page.getByLabel('真实来源统计')
  await expect(stats).toContainText('用量信息加载失败')
  await expect(stats).toContainText('暂不支持')
  await expect(stats).not.toContainText('0 tokens')
  await expect(stats).not.toContainText('0 CNY')
})

test('real zero balance remains zero and a rejected refresh does not retain stale money', async ({ page }) => {
  await setup(page)
  let fail = false
  await page.route('**/api/v1/ai/balance', r => r.fulfill(fail ? { status: 403, json: { detail: 'SECRET_PROVIDER_ERROR_DO_NOT_RENDER' } } : { json: balance('0.0000') }))
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  const stats = page.getByLabel('真实来源统计')
  await expect(stats).toContainText('0.0000')
  fail = true
  await page.getByRole('button', { name: '刷新统计与余额', exact: true }).click()
  await expect(stats).toContainText('余额查询失败')
  await expect(stats).not.toContainText('0.0000')
  await expect(page.getByText('SECRET_PROVIDER_ERROR_DO_NOT_RENDER')).toHaveCount(0)
  fail = false
  await page.getByRole('button', { name: '重查余额', exact: true }).click()
  await expect(stats).toContainText('0.0000')
})

test('metrics fit a narrow screen with precise money values', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await setup(page)
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  await expect(page.getByLabel('真实来源统计')).toContainText('17.1200')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
