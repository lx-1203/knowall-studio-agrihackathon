import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'
const qa = { id: 'math-qa', deck_id: 'math-deck', card_type: 'qa', front: '求 $x_1^2$ 的值', back: '答案 $z_7$', hints: '使用 $a_2$', tags: [] }
async function prepare(page: Page, card = qa) {
  await page.setViewportSize({ width: 1500, height: 1000 })
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ json: { cards: [card] } }))
  await page.route('**/api/v1/flashcards/decks?*', r => r.fulfill({ json: [{ id: 'math-deck', name: '数学', card_count: 1 }] }))
  await page.goto('/flashcards', { waitUntil: 'domcontentloaded' }); await expect(page.locator('.flashcard-flip')).toBeVisible()
}
for (const kind of ['qa', 'compare']) test(`${kind} front and hint use native math without exposing back`, async ({ page }) => {
  await prepare(page, { ...qa, card_type: kind })
  const face = page.locator('.flashcard-flip')
  await expect(face.locator('.katex')).toHaveCount(2)
  await expect(face).not.toContainText('答案')
  await expect(face).not.toContainText('$x_1^2$')
  await face.click(); await expect(face.locator('.katex')).toHaveCount(1)
  await expect(face).toContainText('答案')
})
test('nonmath front keeps literal markdown and code dollars unchanged', async ({ page }) => {
  const front = 'literal **问题** cell_id `$a_1$` \\$b_2\\$'
  await prepare(page, { ...qa, front, hints: '' })
  await expect(page.locator('.flashcard-flip')).toContainText(front)
  await expect(page.locator('.flashcard-flip .katex')).toHaveCount(0)
})
test('whole cloze formula renders masked before reveal and original after reveal', async ({ page }) => {
  await prepare(page, { ...qa, card_type: 'cloze', front: '$x_1+{{c1::y_9}}=z_2$', hints: '' })
  const face = page.locator('.flashcard-flip')
  await expect(face.locator('.katex')).toHaveCount(1)
  await expect(face).not.toContainText('y_9')
  await expect(face.locator('annotation')).not.toContainText('y_9')
  await face.getByPlaceholder('...').fill('y_9'); await face.getByPlaceholder('...').press('Enter')
  await expect(face.locator('.ant-tag')).toContainText('y_9')
  await expect(face.locator('annotation')).toContainText('x_1+y_9=z_2')
})
test('cloze optional hint is not part of the checked answer', async ({ page }) => {
  await prepare(page, { ...qa, card_type: 'cloze', front: '能量来自 {{c1::$E=mc^2$::能量公式}}', hints: '' })
  const face = page.locator('.flashcard-flip')
  await expect(face).not.toContainText('E=mc^2')
  await face.getByPlaceholder('...').fill('$E=mc^2$'); await face.getByPlaceholder('...').press('Enter')
  await expect(face.locator('.ant-tag')).toHaveClass(/ant-tag-success/)
  await expect(face.locator('.ant-tag .katex')).toHaveCount(1)
  await expect(face.locator('.ant-tag')).not.toContainText('能量公式')
})
test('front math still blocks raw HTML, unsafe links and remote images', async ({ page }) => {
  const requests: string[] = []; page.on('request', r => { if (r.url().startsWith('https://example.test')) requests.push(r.url()) })
  await prepare(page, { ...qa, hints: '', front: '$x_1$ <img src="https://example.test/pixel"> [bad](javascript:alert%281%29) ![alt](https://example.test/pic)' })
  const face = page.locator('.flashcard-flip')
  await expect(face.locator('.katex')).toHaveCount(1)
  await expect(face.locator('img,script,iframe,a[href^="javascript:"]')).toHaveCount(0)
  expect(requests).toEqual([])
})
for (const entry of ['manual', 'edit']) test(`${entry} native form preview matches the saved math draft`, async ({ page }) => {
  await page.route('**/api/v1/flashcards/cards/math-qa', r => r.fulfill({ json: qa }))
  await prepare(page)
  await page.getByRole('button', { name: entry === 'manual' ? '手动添加' : '编辑当前卡片', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('问题 / 正面', { exact: true }).fill('新题 $q_5$')
  await dialog.getByText('卡片预览', { exact: true }).click()
  await expect(dialog.locator('.flashcard-draft-preview .katex:visible')).toHaveCount(entry === 'edit' ? 2 : 1)
  await expect(dialog.locator('.flashcard-draft-preview')).toContainText('新题')
})

test('repeated cloze ordinal reveals both occurrences without exposing the next card', async ({ page }) => {
  let cards = [{ ...qa, card_type: 'cloze', front: '$x_1+{{c1::y_9}}={{c1::y_9}}$', hints: '' },
    { ...qa, id: 'next-math', card_type: 'cloze', front: '$a_2+{{c2::hidden}}=b_3$', hints: '' }]
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ json: { cards } }))
  await page.route('**/api/v1/flashcards/decks?*', r => r.fulfill({ json: [{ id: 'math-deck', name: '数学', card_count: 2 }] }))
  await page.goto('/flashcards', { waitUntil: 'domcontentloaded' })
  const face = page.locator('.flashcard-flip')
  await expect(face.getByPlaceholder('...')).toHaveCount(2)
  await face.getByPlaceholder('...').first().fill('y_9'); await face.getByPlaceholder('...').first().press('Enter')
  await expect(face.locator('.ant-tag-success')).toHaveCount(2)
  await page.getByRole('button', { name: '下一张 (跳过)' }).click()
  await expect(face.getByPlaceholder('...')).toHaveCount(1)
  await expect(face.getByPlaceholder('...')).toHaveValue('')
  await expect(face).not.toContainText('hidden')
})

test('lazy math renderer failure retains masked cloze and working reveal controls', async ({ page }) => {
  await page.route('**/src/components/MarkdownContent.tsx*', r => r.abort())
  await prepare(page, { ...qa, card_type: 'cloze', front: '$x_1+{{c1::secret}}=z_2$', hints: '' })
  const face = page.locator('.flashcard-flip')
  await expect(face.locator('.learning-content-fallback')).toContainText('$x_1+[1]=z_2$')
  await expect(face).not.toContainText('secret')
  await face.getByRole('button', { name: '显示全部答案' }).click()
  await expect(face).toContainText('secret')
  await expect(page.getByRole('button', { name: '正常回忆 (3)' })).toBeVisible()
})

for (const mode of ['light', 'dark']) test(`${mode} display math stays inside the narrow card`, async ({ page }) => {
  await page.addInitScript(mode => localStorage.setItem('knowall-theme', mode), mode)
  await prepare(page, { ...qa, front: '$$\n'+Array(24).fill('x_{123}').join('+')+'\n$$', hints: '' })
  await page.setViewportSize({ width: 720, height: 1000 })
  await expect(page.locator('.flashcard-flip .katex-display')).toBeVisible()
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }))
  expect(sizes.width).toBeLessThanOrEqual(sizes.viewport + 1)
  const prefix = process.env.KNOWALL_MATH_SCREENSHOT_PREFIX || 'round-29'
  await page.screenshot({ path: `../docs/open-source-integration/${prefix}-${mode}-math.png`, fullPage: true })
})

test('unsupported TeX braces in a cloze stay hidden instead of exposing its answer', async ({ page }) => {
  await prepare(page, { ...qa, card_type: 'cloze', front: String.raw`值为 {{c1::$\frac{1}{7}$}}，另一个空 {{c2::secret}}`, hints: '' })
  const face = page.locator('.flashcard-flip')
  await expect(face).not.toContainText('frac')
  await expect(face).not.toContainText('secret')
  await expect(face.locator('annotation')).toHaveCount(0)
  await expect(face).toContainText('填空标记')
})
