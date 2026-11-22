import { test, expect } from './fixtures'
import path from 'node:path'

// Current browser UI with explicit API fixtures; no live backend or model calls.
for (const width of [1280, 375]) {
  test(`PDF authority preserves the verified page link at width ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const url = 'https://ocw.mit.edu/courses/res-18-001-calculus-fall-2023/mitres_18_001_f17_ch02.pdf#page=5'
    const title = 'MIT Calculus（PDF 第5页）'
    const excerpt = 'The derivative of a function is its rate of change.'
    await page.route('**/api/v1/knowledge-network/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith('/documents')) return route.fulfill({ json: { items: [] } })
      if (pathname.endsWith('/discover')) {
        return route.fulfill({ json: { items: [], total: 0 } })
      }
      return route.fulfill({ json: {
      id: 'pdf', name: '导数', subject: '数学', topic: '微分', definition: '函数瞬时变化率。',
      aliases: [], review_status: 'reviewed', in_learning: false, source_count: 1,
      content: { name: '导数', topic: '微分', definition: '函数瞬时变化率。', formulas: [],
        conditions: [], key_points: ['差商极限'], pitfalls: [], prerequisites: [], related: [] },
      sources: [], relations: [], learning: null,
      source_search: { status: 'found', sources: [{ title, url, authority: '高校', excerpt }] },
      } })
    })
    await page.goto('/knowledge-network?concept=pdf&q=导数')
    await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
    const article = page.getByRole('article')
    const link = article.getByRole('link', { name: title, exact: true })
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', url)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(link).toHaveAttribute('rel', /noopener/)
    await expect(article.locator('blockquote')).toHaveText(excerpt)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: path.resolve(`../../docs/knowledge-network/round-03/browser-${width}.png`), fullPage: true })
  })
}
