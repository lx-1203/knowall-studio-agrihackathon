import { test, expect } from './fixtures'

test.describe('Chat Page', () => {
  test('loads and shows chat interface', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/chat')
    await page.waitForLoadState('networkidle')

    // Should show chat input area
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('textarea').or(content.locator('input[type="text"]')).or(content.locator('text=AI').or(content.locator('text=chat'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
