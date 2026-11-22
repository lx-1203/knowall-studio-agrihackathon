import { test, expect } from './fixtures'

test.describe('Knowledge Tree Page', () => {
  test('loads and shows knowledge tree area', async ({ page }) => {
    page.on('pageerror', err => console.warn('Page error:', err.message))

    await page.goto('/knowledge')
    await page.waitForLoadState('networkidle')

    // Page should render with ReactFlow or empty state
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.react-flow').or(content.locator('.ant-empty')).or(content.locator('text=知识树').or(content.locator('text=Knowledge'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })
  })
})
