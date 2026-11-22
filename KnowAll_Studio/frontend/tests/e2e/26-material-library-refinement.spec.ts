import { test, expect } from './fixtures'

test('material sorting and reset preserve a useful library on a narrow screen', async ({ page }) => {
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [
    { id: 'old', filename: 'A 教材.md', status: 'ready', file_type: 'md', subject: '历史', material_kind: 'knowledge', created_at: '2026-10-01T00:00:00' },
    { id: 'new', filename: 'Z 试卷.md', status: 'ready', file_type: 'md', subject: '生物', material_kind: 'question_bank', created_at: '2026-10-11T00:00:00' },
  ] }))
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/materials')
  await expect(page.locator('.material-tile h3').first()).toContainText('Z 试卷')
  await page.locator('.material-library-options .ant-select-selector').click()
  await page.getByText('资料名称', { exact: true }).click()
  await expect(page).toHaveURL(/sort=name/)
  const names = await page.locator('.material-tile h3').allTextContents()
  expect(names.indexOf('A 教材.md')).toBeLessThan(names.indexOf('Z 试卷.md'))
  await page.getByRole('textbox', { name: '搜索我的资料' }).fill('不存在的资料')
  await expect(page.getByText('没有符合筛选的资料')).toBeVisible()
  await page.getByRole('button', { name: '清除筛选', exact: true }).click()
  await expect(page.locator('.material-tile')).toHaveCount(3)
  await expect(page).toHaveURL(/sort=name/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.getByRole('heading', { name: '我的资料', exact: true }).click()
  await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0)
  await page.screenshot({ path: '../../docs/superpowers/plans/2026-10-11-material-library-mobile.png', fullPage: true })
})
