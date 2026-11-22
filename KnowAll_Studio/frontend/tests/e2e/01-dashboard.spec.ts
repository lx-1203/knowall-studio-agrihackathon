import { test, expect } from './fixtures'

test.describe('Dashboard Page', () => {
  test('loads and shows page title', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', err => errors.push(err.message))
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Verify page loaded - should find the app header
    await expect(page.locator('h1').first()).toContainText('KnowAll')

    // The new default workspace opens the material library.
    const content = page.locator('.ant-layout-content')
    await expect(content.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible({ timeout: 15000 })

    // No console errors
    if (errors.length > 0) console.warn('Console errors:', errors)
    expect(errors.filter(e => !e.includes('third-party') && !e.includes('extension'))).toHaveLength(0)
  })

  test('shows navigation sidebar', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Navigation menu should be visible
    const nav = page.locator('.ant-menu').first()
    await expect(nav).toBeVisible()
  })
})
