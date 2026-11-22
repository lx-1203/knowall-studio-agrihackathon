import { test, expect } from './fixtures'

test.describe('Share Page', () => {
  test('loads and shows share interface', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/share')
    await page.waitForLoadState('networkidle')

    // Should show share UI
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-card').or(content.locator('.ant-empty')).or(content.locator('text=Share').or(content.locator('text=分享'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
