import { test, expect } from './fixtures'

test.describe('Quiz Page', () => {
  test('loads and shows quiz interface', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/quiz')
    await page.waitForLoadState('networkidle')

    // Page should render quiz UI or empty state
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-card').or(content.locator('text=quiz').or(content.locator('text=Quiz')).or(content.locator('text=题库'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
