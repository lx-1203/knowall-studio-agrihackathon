import { test, expect } from './fixtures'

test.describe('Game Page', () => {
  test('loads and shows game interface', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/game')
    await page.waitForLoadState('networkidle')

    // Should show game UI
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-card').or(content.locator('.ant-empty')).or(content.locator('text=游戏').or(content.locator('text=Game'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
