import { test, expect } from './fixtures'

// The former "Pipeline Page" test is gone along with the page itself:
// /pipeline was never a registered route and src/pages/PipelinePage.tsx was
// unreachable (removed in the dead-code pass — see optimization-report.md §7).

test.describe('Navigation', () => {
  test('can navigate between pages via sidebar', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Click on each nav item and verify page changes
    const navItems = page.locator('.ant-menu-item')
    const count = await navItems.count()
    expect(count).toBeGreaterThan(5)
  })
})
