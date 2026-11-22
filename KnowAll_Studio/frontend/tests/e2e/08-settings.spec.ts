import { test, expect } from './fixtures'

test.describe('Settings Page', () => {
  test('loads and shows settings form', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/settings')
    await page.waitForLoadState('networkidle')

    // Should show settings card
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-card').or(content.locator('text=设置').or(content.locator('text=Settings'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
