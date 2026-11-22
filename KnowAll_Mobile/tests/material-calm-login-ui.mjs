// Authorized isolated browser workflow. Fictional credentials; all API traffic intercepted.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const theme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390
const baseURL = process.env.MOBILE_UI_BASE_URL || 'http://localhost:8096'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width, height: 844 }, colorScheme: theme })
const page = await context.newPage(), errors = [], calls = []
page.on('pageerror', error => errors.push(error.message))
await page.route('**/api/**', route => {
  const path = new URL(route.request().url()).pathname
  calls.push(path)
  return route.fulfill({ json: path.endsWith('/auth/login') ? { access_token: 'local-design-test', user: { id: 'calm-login-test', username: 'preview' } } : [] })
})
try {
  await page.goto(`${baseURL}/login`)
  await expect(page.getByText('欢迎回来', { exact: true })).toBeVisible({ timeout: 120000 })
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/ui-01a12efd/00-login-${theme}-${width}.png`, import.meta.url)), fullPage: true })
  await page.getByRole('button', { name: '没有账号？去注册', exact: true }).click()
  await expect(page.getByText('开始你的学习旅程', { exact: true })).toBeVisible()
  await expect(page.locator('input')).toHaveCount(4)
  await page.getByRole('button', { name: '已有账号？去登录', exact: true }).click()
  await expect(page.locator('input')).toHaveCount(2)
  await page.locator('input').nth(0).fill('preview')
  await page.locator('input').nth(1).fill('fictional-preview-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible()
  expect(calls.filter(path => path.endsWith('/auth/login'))).toHaveLength(1)
  expect(calls.every(path => /\/auth\/login$|\/documents\/$/.test(path))).toBeTruthy()
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  console.log(`PASS ${theme}/${width}: login and registration form switch, fictional login to empty library, no overflow or page errors; all HTTP intercepted.`)
} finally { await browser.close() }
