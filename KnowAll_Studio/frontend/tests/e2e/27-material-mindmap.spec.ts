import { test, expect } from './fixtures'

for (const theme of ['light', 'dark']) for (const width of [1440, 375]) test(`material mindmap search, folding and source at ${width}px ${theme}`, async ({ page }) => {
  await page.addInitScript(theme => localStorage.setItem('knowall-theme', theme), theme)
  await page.setViewportSize({ width, height: 900 })
  await page.route('**/api/v1/documents/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/chunks')) return route.fulfill({ json: [{ id: 'c1', index: 0, text: '# 细胞\n细胞结构' }, { id: 'c2', index: 1, text: '# 遗传\nDNA 保存信息' }] })
    if (path.endsWith('/raw')) return route.fulfill({ contentType: 'text/plain', body: '教材原文件' })
    return route.fulfill({ json: { id: 'bio', filename: '生物教材.md', file_type: 'md', status: 'ready' } })
  })
  let empty = false
  await page.route('**/api/v1/learning/**', route => route.fulfill({ json: empty ? null : { id: 'p', sections: [
    { id: 's1', title: '细胞', notes: '细胞结构', chunk_ids: ['c1'], flashcards: [] },
    { id: 's2', title: '遗传', notes: '遗传学基础', chunk_ids: ['c2'], flashcards: [{ id: 'a', front: '遗传信息载体？', back: 'DNA' }] },
  ] } }))
  const openMap = async () => {
    await page.goto('/materials/bio')
    if (width < 1200) await page.getByRole('button', { name: '学习工具', exact: true }).click()
    await page.getByRole('tab', { name: '导图', exact: true }).click()
  }
  await openMap()
  const map = page.getByRole('region', { name: '当前资料导图' })
  await map.getByRole('button', { name: '全部收起' }).click()
  await expect(map.getByText('遗传信息载体？', { exact: true })).toBeHidden()
  await map.getByRole('textbox', { name: '搜索导图' }).fill('dna')
  await expect(map.getByText('遗传信息载体？', { exact: true })).toBeVisible()
  await expect(map.getByRole('button', { name: '收起分支 细胞' })).toHaveCount(0)
  await map.getByRole('button', { name: '查看卡片答案' }).click()
  await expect(map.getByText('DNA', { exact: true })).toBeVisible()
  await page.screenshot({ path: `../../KnowAll_Mobile/docs/patrol/round02-web-map-${width}-${theme}.png`, fullPage: true })
  await map.getByRole('button', { name: '阅读分支原文' }).click()
  await expect(page.getByRole('article', { name: '资料正文' })).toContainText('DNA 保存信息')
  empty = true
  await openMap()
  await expect(map.getByText(/原文结构/)).toBeVisible()
  await map.getByRole('textbox', { name: '搜索导图' }).fill('没有这样的章节')
  await expect(map.getByText('没有匹配的分支')).toBeVisible()
  await map.getByRole('button', { name: '清空搜索' }).click()
  await expect(map.getByRole('button', { name: '阅读分支原文' })).toHaveCount(2)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
