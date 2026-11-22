import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

const back = '**供能要点**\n\n| 结构 | 空列 | 功能 |\n|---|---|---|\n| 线粒体 | | 供能 |\n| A\\|B | 无 | 遗传 |\n\n```python\nif energy < 2:\n    print("供能")\n```\n\n- 细胞呼吸\n- 能量供应\n\n$E=mc^2$\n\n[安全资料](https://example.test/biology)\n[危险](javascript:alert%281%29)\n![远程图片](https://example.test/tracker.png)\n<script>alert(1)</script>'
const qa = { id: 'qa', card_type: 'qa', front: 'literal **问题** cell_id', back, tags: [] }
const compare = { ...qa, id: 'compare', card_type: 'compare', front: '细胞结构对比' }
const cloze = { ...qa, id: 'cloze', card_type: 'cloze', front: '{{c1::细胞核}} 保存遗传信息，{{c2::线粒体}} 供能' }
async function prepare(page: Page, cards = [qa, compare, cloze, { ...cloze, id: 'cloze-next', front: '{{c1::叶绿体}} 进行光合作用' }]) {
  await page.setViewportSize({ width: 1500, height: 1000 })
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ json: { cards } }))
  await page.route('**/api/v1/flashcards/decks?*', r => r.fulfill({ json: [{ id: 'deck', name: '生物复习', card_count: cards.length }] }))
  await page.goto('/flashcards'); await expect(page.locator('.flashcard-flip')).toBeVisible()
}
async function rich(page: Page) {
  const face = page.locator('.flashcard-flip')
  await expect(face.locator('table')).toBeVisible()
  await expect(face.locator('table th')).toHaveText(['结构', '空列', '功能'])
  await expect(face.locator('table tbody tr').first().locator('td')).toHaveText(['线粒体', '', '供能'])
  await expect(face.locator('table tbody tr').nth(1).locator('td').first()).toHaveText('A|B')
  await expect(face.locator('pre code')).toContainText('if energy < 2:\n    print("供能")')
  await expect(face.locator('ul li')).toHaveText(['细胞呼吸', '能量供应'])
  await expect(face.locator('strong')).toHaveText('供能要点')
  await expect(face.locator('.katex')).toBeVisible()
}

for (const type of ['qa', 'compare']) test(`${type} hides answer then uses native mixed GFM with empty cells and escaped pipe`, async ({ page }) => {
  await prepare(page, [type === 'qa' ? qa : compare])
  await expect(page.locator('.flashcard-flip table')).toHaveCount(0)
  await expect(page.locator('.flashcard-flip')).not.toContainText('供能要点')
  await page.locator('.flashcard-flip').click(); await rich(page)
  await page.locator('.flashcard-flip').click({ position: { x: 12, y: 12 } })
  await expect(page.locator('.flashcard-flip table')).toHaveCount(0)
  await expect(page.locator('.flashcard-flip')).toContainText(type === 'qa' ? qa.front : compare.front)
})

test('safe native answer ignores raw HTML, dangerous links and remote images', async ({ page }) => {
  const requests: string[] = []; page.on('request', r => { if (r.url().startsWith('https://example.test')) requests.push(r.url()) })
  await prepare(page); await page.locator('.flashcard-flip').click(); await rich(page)
  const face = page.locator('.flashcard-flip')
  await expect(face.locator('script,img,iframe')).toHaveCount(0)
  await expect(face.locator('a[href^="javascript:"]')).toHaveCount(0)
  await expect(face.getByRole('link', { name: '安全资料' })).toHaveAttribute('href', 'https://example.test/biology')
  await expect(face.getByRole('link', { name: '安全资料' })).toHaveAttribute('target', '_blank')
  await expect(face.getByRole('link', { name: '安全资料' })).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(face.locator('ul')).toHaveCSS('list-style-type', 'disc')
  expect(requests).toEqual([])
})

test('code clicks, selection, modified shortcut and link click never flip or rate', async ({ page }) => {
  let ratings = 0; await page.route('**/api/v1/flashcards/review', r => { ratings++; return r.fulfill({ json: {} }) })
  await prepare(page); await page.locator('.flashcard-flip').click(); await rich(page)
  await page.locator('.flashcard-flip pre').click(); await expect(page.locator('.flashcard-flip table')).toBeVisible()
  await page.locator('.flashcard-flip pre').evaluate(el => { const range = document.createRange(); range.selectNodeContents(el); const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range) })
  await page.keyboard.press('3'); await page.keyboard.press('ArrowRight')
  await expect(page.locator('.flashcard-flip table')).toBeVisible(); expect(ratings).toBe(0)
  await page.evaluate(() => window.getSelection()?.removeAllRanges())
  await page.keyboard.press('Control+3'); expect(ratings).toBe(0)
  const link = page.getByRole('link', { name: '安全资料' })
  // Keep this hermetic page; only default navigation is prevented, bubbling is real.
  await link.evaluate(el => el.addEventListener('click', e => e.preventDefault()))
  await link.click(); await expect(page.locator('.flashcard-flip table')).toBeVisible()
})

test('search input spaces and rating numbers remain native text editing', async ({ page }) => {
  let ratings = 0; await page.route('**/api/v1/flashcards/review', r => { ratings++; return r.fulfill({ json: {} }) })
  await prepare(page); await page.locator('.flashcard-flip').click()
  const input = page.getByPlaceholder('搜索卡片...'); await input.fill('cell'); await input.press('Space'); await input.press('3')
  await expect(input).toHaveValue('cell 3'); expect(ratings).toBe(0)
  await expect(page.locator('.flashcard-flip')).toContainText('供能要点')
})

test('body shortcut flips and rating preserves submitted card then resets next face', async ({ page }) => {
  const writes: unknown[] = []; await page.route('**/api/v1/flashcards/review', r => { writes.push(r.request().postDataJSON()); return r.fulfill({ json: { next_review_at: '2026-10-10' } }) })
  await prepare(page); await page.keyboard.press('Space'); await rich(page); await page.keyboard.press('3')
  await expect(page.locator('.flashcard-flip')).toContainText(compare.front)
  await expect(page.locator('.flashcard-flip table')).toHaveCount(0)
  expect(writes).toEqual([{ card_id: qa.id, rating: 3 }])
})

test('cloze initially has real blank inputs and no leaked literal answer', async ({ page }) => {
  await prepare(page, [cloze]); const face = page.locator('.flashcard-flip')
  await expect(face.getByPlaceholder('...')).toHaveCount(2)
  await expect(face).not.toContainText('细胞核')
  await face.getByPlaceholder('...').first().fill('细胞核'); await face.getByPlaceholder('...').first().press('Enter')
  await expect(face.locator('.ant-tag').filter({ hasText: '细胞核' })).toBeVisible()
  await face.getByRole('button', { name: '显示全部答案' }).click(); await rich(page)
})

test('cloze spaces/numbers type into input and next card resets revealed state', async ({ page }) => {
  let ratings = 0; await page.route('**/api/v1/flashcards/review', r => { ratings++; return r.fulfill({ json: {} }) })
  await prepare(page, [cloze, { ...cloze, id: 'next', front: '{{c1::叶绿体}} 进行光合作用' }])
  const input = page.locator('.flashcard-flip').getByPlaceholder('...').first()
  await input.fill('cell'); await input.press('Space'); await input.press('3'); await expect(input).toHaveValue('cell 3'); expect(ratings).toBe(0)
  await page.getByRole('button', { name: '显示全部答案' }).click(); await rich(page)
  await page.getByRole('button', { name: '下一张 (跳过)' }).click()
  await expect(page.locator('.flashcard-flip').getByPlaceholder('...')).toHaveCount(1)
  await expect(page.locator('.flashcard-flip').getByPlaceholder('...')).toHaveValue('')
  await expect(page.locator('.flashcard-flip table')).toHaveCount(0)
  await expect(page.locator('.flashcard-flip')).not.toContainText('叶绿体')
})

for (const mode of ['light', 'dark']) test(`${mode} theme and narrow card keep tables/code in their own scroll container`, async ({ page }) => {
  await page.addInitScript(mode => localStorage.setItem('knowall-theme', mode), mode)
  await prepare(page); await page.setViewportSize({ width: 720, height: 1050 })
  await page.locator('.flashcard-flip').click(); await rich(page)
  const dimensions = await page.locator('.flashcard-flip').evaluate(el => { const face = el.firstElementChild!; const rect = el.getBoundingClientRect(); const style = getComputedStyle(face); return { width: rect.width, right: rect.right, background: style.backgroundColor, color: style.color, scroll: document.documentElement.scrollWidth, viewport: innerWidth } })
  expect(dimensions.right).toBeLessThanOrEqual(720); expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.viewport + 1)
  expect(dimensions.background).toBe(mode === 'dark' ? 'rgb(20, 38, 54)' : 'rgb(255, 255, 255)')
  if (mode === 'dark') expect(dimensions.color).toBe('rgb(228, 237, 244)')
})

test('lazy Markdown failure retains literal answer and functioning rating controls', async ({ page }) => {
  await page.route('**/src/components/MarkdownContent.tsx*', r => r.abort())
  await prepare(page); await page.locator('.flashcard-flip').click()
  await expect(page.locator('.flashcard-flip .learning-content-fallback')).toContainText('供能要点')
  await expect(page.getByRole('button', { name: '正常回忆 (3)' })).toBeVisible()
})
