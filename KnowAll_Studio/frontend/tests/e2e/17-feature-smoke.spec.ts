import { test, expect } from './fixtures'

const pages = [
  ['/search', '统一搜索'],
  ['/language', '未选择文档'],
  ['/reading', '自适应渐进式学习'],
  ['/recitation', '背诵练习'],
  ['/live', '实时对话考试'],
  ['/research', '研究历史'],
  ['/books', '教材生成'],
  ['/personal', '个人信息'],
  ['/glossary', '名词解释库'],
] as const

for (const [path, label] of pages) {
  test(`模拟接口下打开 ${path}，显示功能内容且无页面异常`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    if (path === '/glossary') {
      await page.route('**/api/v1/glossary/stats', route =>
        route.fulfill({ json: { total: 0, subjects: [] } }))
      await page.route('**/api/v1/glossary/terms?*', route =>
        route.fulfill({ json: { total: 0, items: [] } }))
    }
    await page.goto(path)
    await expect(page.locator('main').getByText(label, { exact: false }).first()).toBeVisible()
    await expect(page.getByText('页面加载出错')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}

test('词汇学习未选资料时可以跳转资料库', async ({ page }) => {
  await page.goto('/language')
  await page.getByRole('button', { name: '前往资料库' }).click()
  await expect(page).toHaveURL(/\/upload$/)
  await expect(page.locator('main').getByText('资料导入', { exact: true })).toBeVisible()
})

test('模拟接口下打开分子生物学独立刷题页面', async ({ page }) => {
  await page.goto('/molbio-quiz')
  const frame = page.frameLocator('iframe')
  await expect(frame.locator('body')).toContainText('分子生物学')
})
