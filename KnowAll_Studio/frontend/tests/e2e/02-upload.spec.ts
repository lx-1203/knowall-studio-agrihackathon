import { test, expect } from './fixtures'

test.describe('Upload Page', () => {
  test('loads and shows upload area', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', err => errors.push(err.message))

    await page.goto('/upload')
    await page.waitForLoadState('networkidle')

    // Should show the page (even without backend, the page should render)
    const content = page.locator('.ant-layout-content')
    await expect(content.locator('.ant-upload-drag').or(content.locator('text=上传').or(content.locator('text=Upload'))).filter({ visible: true }).first()).toBeVisible({ timeout: 15000 })

    if (errors.filter(e => !e.includes('third-party')).length > 0) console.warn('Errors:', errors)
  })
})
