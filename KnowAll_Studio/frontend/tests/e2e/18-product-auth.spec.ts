import { test, expect } from '@playwright/test'
import { stubBackend, E2E_USER } from './fixtures'

test.use({ video: 'off' })
test.beforeEach(async ({ page }) => {
  await stubBackend(page)
  await page.route('**/api/v1/auth/local-session', route => route.fulfill({ json: { enabled: false } }))
  await page.route('**/api/v1/oauth/providers', route => route.fulfill({ json: { providers: [] } }))
})

test('account login opens the workspace and logout returns to the labeled form', async ({ page }) => {
  await page.route('**/api/v1/auth/login', route => route.fulfill({ json: { access_token: 'product-test-token', user: E2E_USER } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎回到智识工坊' })).toBeVisible()
  await page.getByLabel('账号', { exact: true }).fill('e2e')
  await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('navigation', { name: '学习导航' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'KnowAll Studio' })).toBeVisible()
  await page.screenshot({ path: '.tmp/product-workspace-desktop.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '登出', exact: true }).click()
  await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('knowall_token'))).toBeNull()
})

test('failed login keeps a readable error next to the form', async ({ page }) => {
  await page.route('**/api/v1/auth/login', route => route.fulfill({ status: 401, json: { detail: '账号或密码不正确' } }))
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill('unknown')
  await page.getByLabel('密码', { exact: true }).fill('wrong-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '账号或密码不正确' })).toBeVisible()
  await expect(page.getByLabel('账号', { exact: true })).toHaveValue('unknown')
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeEnabled()
})

test('register validates confirmation before creating an account', async ({ page }) => {
  let requests = 0
  await page.route('**/api/v1/auth/register', route => { requests++; return route.fulfill({ json: { access_token: 'register-test-token', user: E2E_USER } }) })
  await page.goto('/')
  await page.getByRole('tab', { name: '注册', exact: true }).click()
  await page.getByLabel('用户名', { exact: true }).fill('e2e')
  await page.getByLabel('邮箱', { exact: true }).fill('e2e@example.com')
  await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
  await page.getByLabel('确认密码', { exact: true }).fill('different')
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await expect(page.getByText('两次输入的密码不一致', { exact: true })).toBeVisible()
  expect(requests).toBe(0)
  await page.getByLabel('确认密码', { exact: true }).fill('safe-test-password')
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await expect(page.getByRole('navigation', { name: '学习导航' })).toBeVisible()
  expect(requests).toBe(1)
})

test('login remains usable at narrow width in both themes', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '.tmp/product-login-mobile.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('.ant-input-affix-wrapper').first()).toHaveCSS('background-color', 'rgb(20, 38, 54)')
  await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  await page.screenshot({ path: '.tmp/product-login-mobile-dark.png', fullPage: true, animations: 'disabled' })
})

test('login desktop presents the study workflow without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '把知识变成自己的。' })).toBeVisible()
  await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '.tmp/product-login-desktop.png', fullPage: true, animations: 'disabled' })
})

test('pending login blocks switching forms and duplicate submissions', async ({ page }) => {
  let release!: () => void
  const responseReady = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/auth/login', async route => {
    await responseReady
    await route.fulfill({ status: 401, json: { detail: '账号或密码不正确' } })
  })
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill('e2e')
  await page.getByLabel('密码', { exact: true }).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  try {
    await expect(page.getByLabel('账号', { exact: true })).toBeDisabled()
    await expect(page.getByRole('tab', { name: '注册', exact: true })).toHaveAttribute('aria-disabled', 'true')
  } finally { release() }
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeEnabled()
})

test('new passwords reject short and overlong multibyte values locally', async ({ page }) => {
  let requests = 0
  await page.route('**/api/v1/auth/register', route => { requests++; return route.fulfill({ json: {} }) })
  await page.goto('/')
  await page.getByRole('tab', { name: '注册', exact: true }).click()
  await page.getByLabel('用户名', { exact: true }).fill('e2e')
  await page.getByLabel('邮箱', { exact: true }).fill('e2e@example.com')
  await page.getByLabel('密码', { exact: true }).fill('1234567')
  await page.getByLabel('确认密码', { exact: true }).fill('1234567')
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await expect(page.getByText('密码至少 8 个字符', { exact: true })).toBeVisible()
  const longPassword = '学'.repeat(25)
  await page.getByLabel('密码', { exact: true }).fill(longPassword)
  await page.getByLabel('确认密码', { exact: true }).fill(longPassword)
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await expect(page.getByText('密码最多 72 字节（中文约 24 字）', { exact: true })).toBeVisible()
  expect(requests).toBe(0)
})


