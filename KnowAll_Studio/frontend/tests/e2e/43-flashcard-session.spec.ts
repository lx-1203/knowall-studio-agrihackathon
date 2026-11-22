import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

const cards = [0, 1].map(i => ({ id: `session-${i}`, card_type: 'qa', front: `本轮问题${i}`, back: `本轮答案${i}`, tags: [] }))
function gate() { let release!: () => void; const promise = new Promise<void>(r => { release = r }); return { release, promise, started: false, ended: false } }
async function open(page: Page, values = cards) {
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ json: { cards: values, due_count: values.length } }))
  await page.goto('/flashcards'); await expect(page.locator('.flashcard-flip')).toBeVisible({ timeout: 90000 })
}
async function grade(page: Page) {
  await page.locator('.flashcard-flip').click(); await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).click()
}
async function progress(page: Page, reviewed: number, due: number) {
  await expect(page.getByTestId('flashcard-session-progress')).toHaveText(`本轮已复习 ${reviewed} 张，待复习 ${due} 张`)
}

test('last card completes this batch and body shortcuts cannot rate it again', async ({ page }) => {
  const sent: unknown[] = []; await page.route('**/api/v1/flashcards/review', r => { sent.push(r.request().postDataJSON()); return r.fulfill({ json: { review_count: 1, next_review_at: '2026-10-10' } }) })
  await open(page, [cards[0]]); await grade(page)
  await expect(page.getByText('本轮复习完成', { exact: true })).toBeVisible()
  await expect(page.locator('.flashcard-flip')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '正常回忆 (3)', exact: true })).toHaveCount(0)
  await page.keyboard.press('Space'); await page.keyboard.press('3')
  expect(sent).toEqual([{ card_id: cards[0].id, rating: 3 }])
})

test('two successful ratings remove completed cards and only successful writes advance progress', async ({ page }) => {
  await page.route('**/api/v1/flashcards/review', r => r.fulfill({ json: { next_review_at: '2026-10-10' } }))
  await open(page); await progress(page, 0, 2); await grade(page)
  await expect(page.locator('.flashcard-flip')).toContainText(cards[1].front); await progress(page, 1, 1)
  await expect(page.getByRole('button', { name: /上一张$/ })).toBeDisabled()
  await page.keyboard.press('ArrowLeft'); await expect(page.locator('.flashcard-flip')).not.toContainText(cards[0].front)
  await grade(page); await expect(page.getByText('本轮复习完成', { exact: true })).toBeVisible(); await expect(page.getByText('已复习 2 张卡片', { exact: true })).toBeVisible()
})

test('skip navigation does not count as a review and grading last card returns to remaining first', async ({ page }) => {
  const sent: unknown[] = []; await page.route('**/api/v1/flashcards/review', r => { sent.push(r.request().postDataJSON()); return r.fulfill({ json: {} }) })
  await open(page); await page.getByRole('button', { name: /下一张 \(跳过\)$/ }).click()
  await expect(page.locator('.flashcard-flip')).toContainText(cards[1].front); await progress(page, 0, 2)
  await expect(page.getByRole('button', { name: /下一张 \(跳过\)$/ })).toBeDisabled()
  await grade(page); await expect(page.locator('.flashcard-flip')).toContainText(cards[0].front); await progress(page, 1, 1)
  expect(sent).toEqual([{ card_id: cards[1].id, rating: 3 }])
})

test('rating failure retains revealed card and remaining count until explicit successful retry', async ({ page }) => {
  let writes = 0; await page.route('**/api/v1/flashcards/review', r => { writes++; return writes === 1 ? r.fulfill({ status: 503, json: { detail: 'temporary' } }) : r.fulfill({ json: {} }) })
  await open(page); await grade(page); await expect(page.getByText('提交失败', { exact: true })).toBeVisible()
  await expect(page.locator('.flashcard-flip')).toContainText(cards[0].back); await progress(page, 0, 2); expect(writes).toBe(1)
  await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).click()
  await expect(page.locator('.flashcard-flip')).toContainText(cards[1].front); await progress(page, 1, 1); expect(writes).toBe(2)
})

test('pending last rating keeps the face until response and accepts only one write', async ({ page }) => {
  const held = gate(); let writes = 0
  await page.route('**/api/v1/flashcards/review', async r => { writes++; held.started = true; await held.promise; await r.fulfill({ json: {} }) })
  await open(page, [cards[0]]); await grade(page); await expect.poll(() => held.started).toBe(true)
  await expect(page.getByRole('button', { name: '正常回忆 (3)', exact: true })).toBeDisabled()
  await page.keyboard.press('3'); await expect(page.getByText('本轮复习完成', { exact: true })).toHaveCount(0)
  held.release(); await expect(page.getByText('本轮复习完成', { exact: true })).toBeVisible(); expect(writes).toBe(1)
})

test('completed batch explicitly checks genuine due endpoint and new batch resets its count', async ({ page }) => {
  let loads = 0
  await page.route('**/api/v1/flashcards/due?*', r => { loads++; return r.fulfill({ json: { cards: [cards[loads === 1 ? 0 : 1]] } }) })
  await page.route('**/api/v1/flashcards/review', r => r.fulfill({ json: {} }))
  await page.goto('/flashcards'); await expect(page.locator('.flashcard-flip')).toBeVisible(); await grade(page)
  await expect(page.getByText('本轮复习完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '检查待复习卡片', exact: true }).click()
  await expect(page.locator('.flashcard-flip')).toContainText(cards[1].front); await progress(page, 0, 1); expect(loads).toBe(2)
})

test('server empty after completed batch is genuine no-due and not a fake completed day', async ({ page }) => {
  let loads = 0; await page.route('**/api/v1/flashcards/due?*', r => { loads++; return r.fulfill({ json: { cards: loads === 1 ? [cards[0]] : [] } }) })
  await page.route('**/api/v1/flashcards/review', r => r.fulfill({ json: {} }))
  await page.goto('/flashcards'); await expect(page.locator('.flashcard-flip')).toBeVisible(); await grade(page)
  await page.getByRole('button', { name: '检查待复习卡片', exact: true }).click()
  await expect(page.getByText('暂无待复习卡片', { exact: true })).toBeVisible()
  await expect(page.getByText('本轮复习完成', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '检查待复习卡片', exact: true })).toBeEnabled(); expect(loads).toBe(2)
})

test('cloze can finish its last card through existing direct rating controls', async ({ page }) => {
  await page.route('**/api/v1/flashcards/review', r => r.fulfill({ json: {} }))
  await open(page, [{ ...cards[0], card_type: 'cloze', front: '{{c1::叶绿体}} 进行光合作用' }])
  await expect(page.getByPlaceholder('...', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).click()
  await expect(page.getByText('本轮复习完成', { exact: true })).toBeVisible(); await expect(page.getByPlaceholder('...', { exact: true })).toHaveCount(0)
})

test('same-account deck navigation aborts late old rating and cannot alter new deck session', async ({ page }) => {
  const held = gate(); const dueURLs: string[] = []
  await page.route('**/api/v1/flashcards/due?*', r => { dueURLs.push(r.request().url()); return r.fulfill({ json: { cards: r.request().url().includes('deck_id=B') ? [cards[1]] : [cards[0]] } }) })
  await page.route('**/api/v1/flashcards/review', async r => { held.started = true; await held.promise; await r.fulfill({ json: {} }).catch(() => {}); held.ended = true })
  await page.goto('/flashcards?deck_id=A'); await expect(page.locator('.flashcard-flip')).toBeVisible(); await grade(page); await expect.poll(() => held.started).toBe(true)
  await page.evaluate(() => { history.pushState(null, '', '/flashcards?deck_id=B'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.locator('.flashcard-flip')).toContainText(cards[1].front)
  held.release(); await expect.poll(() => held.ended).toBe(true); await progress(page, 0, 1)
  expect(dueURLs.some(u => u.includes('deck_id=B'))).toBe(true)
})

test('same-account deck navigation cancels stale due result and initially hides old deck card', async ({ page }) => {
  const held = gate(); await page.route('**/api/v1/flashcards/due?*', async r => {
    if (r.request().url().includes('deck_id=B')) { held.started = true; await held.promise; await r.fulfill({ json: { cards: [cards[1]] } }); held.ended = true }
    else await r.fulfill({ json: { cards: [cards[0]] } })
  })
  await page.goto('/flashcards?deck_id=A'); await expect(page.locator('.flashcard-flip')).toContainText(cards[0].front)
  await page.evaluate(() => { history.pushState(null, '', '/flashcards?deck_id=B'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect.poll(() => held.started).toBe(true); await expect(page.locator('.flashcard-flip')).toHaveCount(0)
  held.release(); await expect(page.locator('.flashcard-flip')).toContainText(cards[1].front); await progress(page, 0, 1)
})

test('failed completion refresh shows recoverable failure and retry does not fabricate reviews', async ({ page }) => {
  let loads = 0; await page.route('**/api/v1/flashcards/due?*', r => { loads++; return loads === 1 ? r.fulfill({ json: { cards: [cards[0]] } }) : loads <= 4 ? r.fulfill({ status: 503, json: { detail: 'temporary' } }) : r.fulfill({ json: { cards: [] } }) })
  await page.route('**/api/v1/flashcards/review', r => r.fulfill({ json: {} }))
  await page.goto('/flashcards'); await expect(page.locator('.flashcard-flip')).toBeVisible(); await grade(page)
  await page.getByRole('button', { name: '检查待复习卡片', exact: true }).click()
  await expect(page.getByText('待复习卡片加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('本轮复习完成', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '重新加载卡片', exact: true }).click()
  await expect(page.getByText('暂无待复习卡片', { exact: true })).toBeVisible()
})
