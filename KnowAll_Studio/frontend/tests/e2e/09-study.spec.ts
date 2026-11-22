import { test, expect } from './fixtures'

test.describe('Study Plan Page', () => {
  test('loads and shows study plan interface', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/study')
    await page.waitForLoadState('networkidle')

    // Should show study plan UI
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-card').or(content.locator('.ant-empty')).or(content.locator('text=Study').or(content.locator('text=学习计划'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
