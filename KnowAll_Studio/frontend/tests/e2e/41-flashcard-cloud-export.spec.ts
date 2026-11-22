import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'

const deck = { id: 'private-deck', name: '细胞结构', card_count: 2, created_at: '2026-10-09' }
const cards = [0, 1].map(i => ({ id: `card-${i}`, front: `private-question-${i}`, back: `private-answer-${i}`, card_type: 'qa', tags: [] }))
const exportPath = '**/api/v1/flashcards/export/anki/*'
function gate() { let release!: () => void; const promise = new Promise<void>(r => { release = r }); return { promise, release, started: false, ended: false } }
async function exportButton(page: Page) { return page.getByRole('row').filter({ hasText: deck.name }).getByRole('button', { name: /导出Anki$/ }) }
test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 950 })
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ json: { cards } }))
  await page.route('**/api/v1/flashcards/decks?*', r => r.fulfill({ json: [deck] }))
})

test('private APKG download uses current Bearer and Chinese filename', async ({ page }) => {
  let auth: string | undefined
  await page.route(exportPath, r => { auth = r.request().headers().authorization; return r.fulfill({ contentType: 'application/zip', body: Buffer.from('PK-fixture-private-deck') }) })
  await page.goto('/flashcards'); const download = page.waitForEvent('download'); await (await exportButton(page)).click()
  const file = await download; expect(auth).toBe(`Bearer ${E2E_TOKEN}`); expect(file.suggestedFilename()).toBe('细胞结构.apkg')
})

for (const [endpoint, title, retry, empty] of [
  ['due', '待复习卡片加载失败', '重新加载卡片', '暂无待复习卡片'],
  ['decks', '牌组列表加载失败', '重新加载牌组', '暂无牌组'],
]) test(`${endpoint} failure is not successful empty and manual retry restores real UI`, async ({ page }) => {
  let failure = true
  await page.route(`**/api/v1/flashcards/${endpoint}?*`, r => r.fulfill(failure ? { status: 403, json: { detail: 'secret-server-detail' } } : { json: endpoint === 'due' ? { cards } : [deck] }))
  await page.goto('/flashcards'); await expect(page.getByText(title, { exact: true })).toBeVisible(); await expect(page.getByText(empty, { exact: true })).toHaveCount(0)
  await expect(page.getByText('secret-server-detail')).toHaveCount(0); failure = false
  await page.getByRole('button', { name: retry, exact: true }).click(); await expect(page.getByText(title, { exact: true })).toHaveCount(0)
  await expect(page.getByText(endpoint === 'due' ? cards[0].front : deck.name, { exact: true })).toBeVisible()
})

for (const status of [403, 503]) test(`${status} export failure retains account and permits manual recovery with native retry`, async ({ page }) => {
  let failure = true; let calls = 0
  await page.route(exportPath, r => { calls++; return r.fulfill(failure ? { status, json: { detail: 'secret-export-detail' } } : { contentType: 'application/zip', body: Buffer.from('PK-fixture') }) })
  await page.goto('/flashcards'); await (await exportButton(page)).click()
  await expect(page.getByText('Anki 导出失败', { exact: true })).toBeVisible(); expect(calls).toBe(status === 503 ? 3 : 1)
  await expect(page.locator('.product-account')).toBeVisible(); await expect(page.getByText('secret-export-detail')).toHaveCount(0)
  failure = false; const file = page.waitForEvent('download'); await page.getByRole('button', { name: '重新导出', exact: true }).click(); await file
  await expect(page.getByText('Anki 导出失败', { exact: true })).toHaveCount(0)
})

test('401 APKG response uses existing logout flow', async ({ page }) => {
  await page.route(exportPath, r => r.fulfill({ status: 401, json: { detail: 'expired' } }))
  await page.goto('/flashcards'); await (await exportButton(page)).click(); await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('knowall_user'))).toBeNull()
})

for (const boundary of ['logout', 'leave']) test(`pending APKG ${boundary} aborts HTTP and never downloads old file`, async ({ page }) => {
  const held = gate(); const files: string[] = []; let aborted = false
  page.on('download', d => files.push(d.suggestedFilename())); page.on('requestfailed', r => { if (r.url().includes('/export/anki/')) aborted = true })
  await page.route(exportPath, async r => { held.started = true; await held.promise; await r.fulfill({ contentType: 'application/zip', body: Buffer.from('PK-old-private') }).catch(() => {}); held.ended = true })
  await page.goto('/flashcards'); await (await exportButton(page)).click(); await expect.poll(() => held.started).toBe(true)
  if (boundary === 'logout') await page.getByRole('button', { name: '登出', exact: true }).click()
  else {
    await page.getByRole('link', { name: '学习进度', exact: true }).click()
    // A lazy route can retain the old mounted page until navigation commits.
    await expect(page.getByText('学习仪表盘', { exact: true })).toBeVisible()
  }
  held.release(); await expect.poll(() => held.ended).toBe(true); await expect.poll(() => aborted).toBe(true); expect(files).toEqual([])
})

test('pending download disables both entries and sends only one HTTP request', async ({ page }) => {
  const held = gate(); let calls = 0
  await page.route(exportPath, async r => { calls++; held.started = true; await held.promise; await r.fulfill({ contentType: 'application/zip', body: Buffer.from('PK-fixture') }) })
  await page.goto('/flashcards'); const button = await exportButton(page); await button.click(); await expect.poll(() => held.started).toBe(true)
  await expect(button).toBeDisabled(); await expect(page.getByRole('button', { name: /导出Anki$/ }).first()).toBeDisabled()
  expect(calls).toBe(1); held.release(); await expect(button).toBeEnabled()
})

test('pending rating locks refresh and limit then advances only reviewed card', async ({ page }) => {
  const held = gate(); let loads = 0
  await page.route('**/api/v1/flashcards/due?*', r => { loads++; return r.fulfill({ json: { cards } }) })
  await page.route('**/api/v1/flashcards/review', async r => { held.started = true; await held.promise; await r.fulfill({ json: { next_review_at: '2026-10-10' } }) })
  await page.goto('/flashcards'); await page.locator('.flashcard-flip').click(); await page.getByRole('button', { name: /正常回忆/ }).click(); await expect.poll(() => held.started).toBe(true)
  await expect(page.getByRole('button', { name: /刷新$/ })).toBeDisabled(); await expect(page.locator('.ant-select').first()).toHaveClass(/ant-select-disabled/)
  expect(loads).toBe(1); held.release(); await expect(page.getByText(cards[1].front, { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /刷新$/ })).toBeEnabled()
})

test('direct account replacement clears local deck names, private input and open generation dialog before B responds', async ({ page }) => {
  const held = gate()
  await page.route('**/api/v1/flashcards/decks?*', async r => {
    if (r.request().headers().authorization === 'Bearer B-token') {
      held.started = true; await held.promise; await r.fulfill({ json: [{ ...deck, id: 'B-deck', name: 'B-private-deck' }] }); held.ended = true
    } else await r.fulfill({ json: [deck] })
  })
  await page.goto('/flashcards'); await expect(page.getByText(deck.name, { exact: true })).toBeVisible()
  await page.getByPlaceholder('搜索卡片...').fill('A-private-query')
  await page.getByRole('button', { name: /AI 生成卡片$/ }).click()
  await page.getByPlaceholder('粘贴知识点内容，AI 将据此生成问答/填空/对比卡片...').fill('A-private-generation-input')
  await page.evaluate(async () => {
    const { useAuthStore } = await import('/src/stores/index.ts')
    useAuthStore.getState().login('B-token', { id: 'B', username: 'B', email: 'B@example.test' })
  })
  await expect.poll(() => held.started).toBe(true); await expect(page.locator('.product-account')).toHaveText('B')
  await expect(page.getByText(deck.name, { exact: true })).toHaveCount(0)
  await expect(page.getByPlaceholder('搜索卡片...')).toHaveValue('')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  held.release(); await expect(page.getByText('B-private-deck', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /AI 生成卡片$/ }).click()
  await expect(page.getByPlaceholder('粘贴知识点内容，AI 将据此生成问答/填空/对比卡片...')).toHaveValue('')
})

test('same-account token refresh preserves local input and generation dialog', async ({ page }) => {
  await page.goto('/flashcards'); await expect(page.getByText(deck.name, { exact: true })).toBeVisible()
  await page.getByPlaceholder('搜索卡片...').fill('same-account-query')
  await page.getByRole('button', { name: /AI 生成卡片$/ }).click()
  await page.getByPlaceholder('粘贴知识点内容，AI 将据此生成问答/填空/对比卡片...').fill('same-account-generation-input')
  await page.evaluate(async () => {
    const { useAuthStore } = await import('/src/stores/index.ts')
    const current = useAuthStore.getState()
    current.login('rotated-token', { ...current.user, username: 'updated-profile' })
  })
  await expect(page.locator('.product-account')).toHaveText('updated-profile')
  await expect(page.getByPlaceholder('搜索卡片...')).toHaveValue('same-account-query')
  await expect(page.getByPlaceholder('粘贴知识点内容，AI 将据此生成问答/填空/对比卡片...')).toHaveValue('same-account-generation-input')
})
