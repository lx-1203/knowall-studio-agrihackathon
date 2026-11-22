import { test, expect } from './fixtures'

test.describe('Flashcard Page', () => {
  test('loads and shows flashcard interface', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/flashcards')
    await page.waitForLoadState('networkidle')

    // Should show flashcard UI or empty state
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-card').or(content.locator('.ant-empty')).or(content.locator('text=闪卡').or(content.locator('text=Flashcard'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
