import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mockShell } from './mobile-shell-fixture.mjs'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const theme = process.env.MOBILE_UI_THEME || 'light'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: theme })
await mockShell(page)
try {
  for (const [route, name, label] of [['upload', '上传', '资料导入'], ['review', '复习', '复习与巩固'], ['study', '计划', '期末复习 · 细胞生物学'], ['profile', '我的', '小林']]) {
    await page.goto(`http://localhost:8096/${route}`)
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible({ timeout: 120000 })
    await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/ui02-01a12efd/before-${route}-${theme}.png`, import.meta.url)), fullPage: true })
    console.log(`CAPTURED ${name}/${theme}`)
  }
} finally { await browser.close() }
