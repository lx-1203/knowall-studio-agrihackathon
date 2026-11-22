import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
const session = { enabled: true, access_token: 'local-session-token', user: {
  id: 'local-user', username: '本地用户', email: 'local@knowall.invalid', created_at: new Date(0).toISOString(),
} }
test.use({ video: 'off' })
test.beforeEach(async ({ page }) => { await stubBackend(page) })
test('fresh local launch enters dashboard, carries token and survives refresh', async ({ page }) => {
  const headers: string[] = []
  await page.route('**/api/v1/auth/local-session', route => {
    expect(route.request().headers()['x-knowall-local']).toBe('1')
    return route.fulfill({ json: session })
  })
  page.on('request', req => { if (req.url().includes('/api/') && !req.url().endsWith('/local-session')) headers.push(req.headers()['authorization']) })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
  await expect(page.getByRole('button', { name: '登出' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: '登录', exact: true })).toHaveCount(0)
  await expect.poll(() => headers.includes('Bearer local-session-token')).toBe(true)
  await expect(page.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/local-session-desktop.png' })
  await page.getByRole('banner').screenshot({ path: 'test-results/knowall-brand.png' })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
  await expect(page.getByRole('img', { name: '智识工坊 KnowAll 标志' })).toHaveAttribute('src', '/logo.svg')
})
test('disabled local mode preserves normal login', async ({ page }) => {
  await page.route('**/api/v1/auth/local-session', route => route.fulfill({ json: { enabled: false } }))
  await page.goto('/')
  await expect(page.getByRole('tab', { name: '登录', exact: true })).toBeVisible()
})
test('failed bootstrap offers retry without inventing a session', async ({ page }) => {
  let fail = true
  await page.route('**/api/v1/auth/local-session', route => route.fulfill({ status: fail ? 503 : 200, json: fail ? { detail: '数据库暂时不可用' } : session }))
  await page.goto('/')
  await expect(page.getByRole('button', { name: '重新连接' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('knowall_token'))).toBeNull()
  fail = false
  await page.getByRole('button', { name: '重新连接' }).click()
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
})
test('expired local session reopens automatically', async ({ page }) => {
  let sessions = 0
  await page.route('**/api/v1/auth/local-session', route => { sessions++; return route.fulfill({ json: session }) })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new Event('auth:logout')))
  await expect.poll(() => sessions).toBe(2)
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
})
test('mobile brand and controls fit the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**/api/v1/auth/local-session', route => route.fulfill({ json: session }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/local-session-mobile.png' })
})

