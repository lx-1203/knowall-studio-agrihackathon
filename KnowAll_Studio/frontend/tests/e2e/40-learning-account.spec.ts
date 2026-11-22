import { test, expect, type Page } from '@playwright/test'
import { stubBackend } from './fixtures'

const question = (id: string) => ({ id: `${id}-q`, question_type: 'single_choice', question_text: `${id}-private-question`, options: [{ label: 'A', text: '正确' }, { label: 'B', text: '其他' }], answer: 'A', analysis: '来源', difficulty: 'medium', tags: [] })
const exam = (id: string) => ({ paper_id: `${id}-paper`, title: `${id}-private-exam`, question_count: 1, questions: [question(id)] })
const cards = (id: string) => [0, 1].map(i => ({ id: `${id}-card-${i}`, card_type: 'qa', front: `${id}-private-card-${i}`, back: `${id}-private-answer-${i}`, hints: '', tags: [] }))
const passage = (id: string) => ({ id: `${id}-passage`, title: `${id}-private-passage`, content: `${id}账号原文`, source: 'temporary source', category: 'custom', difficulty: 1, tags: [] })
const plan = (id: string) => ({ segments: [{ text: `${id}账号遮字`, hidden: false }], total_chars: 6, hidden_chars: 0 })
const dictation = (id: string) => ({ record_id: `${id}-record`, accuracy: 1, correct_chars: 6, missing_chars: 0, extra_chars: 0, original_length: 6, input_length: 6, diffs: [] })
const score = (id: string) => ({ paper_id: `${id}-paper`, score: 5, total: 1, correct: 1, percentage: 100, details: [{ question_id: `${id}-q`, is_correct: true, user_answer: 'A', correct_answer: 'A' }] })
const account = (request: { headers(): Record<string, string> }) => request.headers().authorization?.split(' ')[1]?.split('-')[0] || 'A'
async function login(page: Page, id: string) {
  await page.getByLabel('账号', { exact: true }).fill(id)
  await page.getByLabel('密码', { exact: true }).fill('temporary-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.product-account')).toHaveText(id)
}
async function switchB(page: Page) { await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, 'B') }
async function state(page: Page, action = 'read') {
  return page.evaluate(async action => {
    // Actual Vite store APIs for state contracts only; no product debug bridge.
    const { useAuthStore: auth, useQuizStore: quiz, useFlashcardStore: flash, useRecitationStore: rec } = await import('/src/stores/index.ts')
    if (action === 'seed') {
      quiz.setState({ currentExam: { paper_id: 'A-paper' }, userAnswers: { 'A-q': 'A' }, results: { score: 5 } })
      flash.setState({ dueCards: [{ id: 'A-card', front: 'A-private-card' }], currentIndex: 1, isFlipped: true }); flash.getState().setReviewLimit(50)
      rec.setState({ passages: [{ id: 'A-passage' }], currentPassage: { id: 'A-passage', content: 'A原文' }, mode: 'dictation', hideLevel: 4, hidePlan: { segments: [] }, dictationInput: 'A默写', dictationResult: { accuracy: 1 }, revealedIndices: new Set([1]), allRevealed: true, hideRevealedSegments: new Set([1]) })
      localStorage.setItem('round17-note', 'keep-note')
    }
    if (action === 'logout') auth.getState().logout()
    if (action === 'rotate') auth.getState().login('A-rotated-token', { ...auth.getState().user, username: 'updated' })
    if (action === 'directB') auth.getState().login('B-token', { id: 'B', username: 'B', email: 'B@example.test' })
    const q = quiz.getState(), f = flash.getState(), p = rec.getState()
    return { exam: q.currentExam, answers: q.userAnswers, results: q.results, cards: f.dueCards, index: f.currentIndex, flipped: f.isFlipped, limit: f.reviewLimit, passage: p.currentPassage, passages: p.passages, hidePlan: p.hidePlan, mode: p.mode, hideLevel: p.hideLevel, input: p.dictationInput, dictationResult: p.dictationResult, revealed: [...p.revealedIndices], allRevealed: p.allRevealed, hideRevealed: [...p.hideRevealedSegments], actions: [typeof q.setCurrentExam, typeof f.setDueCards, typeof p.setCurrentPassage], note: localStorage.getItem('round17-note'), diskLimit: localStorage.getItem('knowall_review_limit') }
  }, action)
}
function gate() { let release!: () => void; const promise = new Promise<void>(r => { release = r }); return { promise, release, started: false, ended: false } }
async function startExam(page: Page, id: string) {
  await page.getByRole('tab', { name: /题库/ }).click()
  await page.getByRole('button', { name: /一键刷题/ }).click()
  await expect(page.getByText(`${id}-private-exam`, { exact: true })).toBeVisible()
}
async function startPassage(page: Page, id: string, mode: 'hide' | 'dictation') {
  const back = page.getByRole('button', { name: '← 返回列表', exact: true })
  if (await back.isVisible()) await back.click()
  await page.getByRole('row').filter({ hasText: `${id}-private-passage` }).getByRole('button', { name: mode === 'hide' ? /遮字背诵$/ : /默\s*写$/ }).click()
}

test.beforeEach(async ({ page }) => {
  await stubBackend(page)
  await page.route('**/api/v1/auth/local-session', r => r.fulfill({ json: { enabled: false } }))
  await page.route('**/api/v1/oauth/providers', r => r.fulfill({ json: { providers: [] } }))
  await page.route('**/api/v1/auth/login', r => { const id = r.request().postDataJSON().username; return r.fulfill({ json: { access_token: `${id}-token`, user: { id, username: id, email: `${id}@example.test` } } }) })
  await page.route('**/api/v1/quiz/questions?*', r => r.fulfill({ json: [question(account(r.request()))] }))
  await page.route('**/api/v1/quiz/exam/create', r => r.fulfill({ json: exam(account(r.request())) }))
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ json: { cards: cards(account(r.request())) } }))
  await page.route('**/api/v1/recitation/passages?*', r => r.fulfill({ json: { passages: [passage(account(r.request()))] } }))
  await page.route('**/api/v1/recitation/stats', r => r.fulfill({ json: { total_passages: 1, total_records: 0, avg_accuracy: 0, mastered: 0 } }))
  await page.route('**/api/v1/recitation/hide-plan', r => r.fulfill({ json: plan(account(r.request())) }))
  await page.setViewportSize({ width: 1600, height: 950 })
})

for (const boundary of ['logout', 'directB']) test(`native ${boundary} clears three learning stores synchronously and preserves actions/preferences/disk`, async ({ page }) => {
  await page.goto('/dashboard'); await login(page, 'A'); await state(page, 'seed')
  const s = await state(page, boundary)
  expect(s).toMatchObject({ exam: null, answers: {}, results: null, cards: [], index: 0, flipped: false, limit: 50, passage: null, passages: [], hidePlan: null, input: '', dictationResult: null, revealed: [], allRevealed: false, hideRevealed: [], mode: 'dictation', hideLevel: 4, actions: ['function', 'function', 'function'], note: 'keep-note', diskLimit: '50' })
})

test('same-account token/profile refresh preserves three learning states and preferences', async ({ page }) => {
  await page.goto('/dashboard'); await login(page, 'A'); const before = await state(page, 'seed'); expect(await state(page, 'rotate')).toEqual(before)
})

test('actual 401 logs out and resets every learning store', async ({ page }) => {
  await page.goto('/dashboard'); await login(page, 'A'); await state(page, 'seed')
  await page.route('**/api/v1/flashcards/due?*', r => r.fulfill({ status: 401, json: { detail: 'expired' } }))
  await page.evaluate(async () => { const { getDueCards } = await import('/src/api.ts'); await getDueCards(20).catch(() => {}) })
  await expect(page.getByLabel('账号', { exact: true })).toBeVisible(); const s = await state(page)
  expect(s.exam).toBeNull(); expect(s.cards).toEqual([]); expect(s.passage).toBeNull(); expect(s.limit).toBe(50)
})

test('real A exam cannot be displayed after logout and B login', async ({ page }) => {
  await page.goto('/quiz'); await login(page, 'A'); await startExam(page, 'A')
  await switchB(page); await page.getByRole('tab', { name: /答题/ }).click()
  await expect(page.getByText('A-private-exam', { exact: true })).toBeHidden()
  await expect(page.getByText('暂无进行中的试卷', { exact: true })).toBeVisible()
})

test('late A exam creation is cancelled and never replaces the real B exam', async ({ page }) => {
  const held = gate(); let cancelled = false
  page.on('requestfailed', r => { if (r.url().endsWith('/quiz/exam/create')) cancelled = true })
  await page.route('**/api/v1/quiz/exam/create', async r => { if (account(r.request()) !== 'A') return r.fulfill({ json: exam('B') }); held.started = true; await held.promise; await r.fulfill({ json: exam('A') }).catch(() => {}); held.ended = true })
  await page.goto('/quiz'); await login(page, 'A'); await page.getByRole('tab', { name: /题库/ }).click(); await page.getByRole('button', { name: /一键刷题/ }).click(); await expect.poll(() => held.started).toBe(true)
  await switchB(page); await startExam(page, 'B'); held.release(); await expect.poll(() => held.ended).toBe(true)
  expect((await state(page)).exam.paper_id).toBe('B-paper'); await expect(page.getByText('A-private-exam', { exact: true })).toBeHidden(); expect(cancelled).toBe(true)
})

test('late A exam submission cannot write results into the B paper', async ({ page }) => {
  const held = gate()
  await page.route('**/api/v1/quiz/exam/submit', async r => { held.started = true; await held.promise; await r.fulfill({ json: score('A') }).catch(() => {}); held.ended = true })
  await page.goto('/quiz'); await login(page, 'A'); await startExam(page, 'A'); await page.getByRole('radio', { name: /正确/ }).check(); await page.getByRole('button', { name: /提交 \(1\/1\)/ }).click(); await expect.poll(() => held.started).toBe(true)
  await switchB(page); await startExam(page, 'B'); held.release(); await expect.poll(() => held.ended).toBe(true)
  const s = await state(page); expect(s.exam.paper_id).toBe('B-paper'); expect(s.results).toBeNull(); expect(s.answers).toEqual({})
})

test('late A quick-generation must not continue save/create POST with B credentials', async ({ page }) => {
  const held = gate(); const writes: string[] = []
  await page.route('**/api/v1/quiz/generate', async r => { held.started = true; await held.promise; await r.fulfill({ json: { questions: [question('A')] } }).catch(() => {}); held.ended = true })
  await page.route('**/api/v1/quiz/bank/save', r => { writes.push(account(r.request())); return r.fulfill({ json: { question_ids: ['A-q'] } }) })
  await page.goto('/quiz'); await login(page, 'A')
  await page.evaluate(async () => { const { useAppStore } = await import('/src/stores/index.ts'); useAppStore.getState().setSelectedDoc('A-doc') })
  await page.getByRole('button', { name: /快速测验$/ }).click(); await expect.poll(() => held.started).toBe(true)
  await switchB(page); held.release(); await expect.poll(() => held.ended).toBe(true)
  await expect.poll(async () => (await state(page)).exam).toBeNull(); expect(writes).toEqual([])
})

test('late A saved preview cannot create a new paper for B', async ({ page }) => {
  const held = gate(); const creates: string[] = []
  await page.route('**/api/v1/quiz/generate', r => r.fulfill({ json: { questions: [question('A')] } }))
  await page.route('**/api/v1/quiz/bank/save', async r => { held.started = true; await held.promise; await r.fulfill({ json: { question_ids: ['A-q'] } }).catch(() => {}); held.ended = true })
  await page.route('**/api/v1/quiz/exam/create', r => { creates.push(account(r.request())); return r.fulfill({ json: exam('A') }) })
  await page.goto('/quiz'); await login(page, 'A'); await page.evaluate(async () => { const { useAppStore } = await import('/src/stores/index.ts'); useAppStore.getState().setSelectedDoc('A-doc') })
  await page.getByRole('button', { name: /生成预览$/ }).click(); await page.getByRole('button', { name: /组卷答题$/ }).click(); await expect.poll(() => held.started).toBe(true)
  await switchB(page); held.release(); await expect.poll(() => held.ended).toBe(true); expect(creates).toEqual([]); expect((await state(page)).exam).toBeNull()
})

test('B due-card failure cannot expose A card/answer after real logout', async ({ page }) => {
  let reads = 0
  await page.route('**/api/v1/flashcards/due?*', r => { if (account(r.request()) === 'A') return r.fulfill({ json: { cards: cards('A') } }); reads++; return r.fulfill({ status: 503, json: { detail: 'temporary unavailable' } }) })
  await page.goto('/flashcards'); await login(page, 'A'); await expect(page.getByText('A-private-card-0', { exact: true })).toBeVisible(); await page.getByText('A-private-card-0', { exact: true }).click(); await expect(page.getByText('A-private-answer-0', { exact: true })).toBeVisible()
  await switchB(page); await expect.poll(() => reads).toBe(3); await expect(page.getByText('A-private-answer-0', { exact: true })).toBeHidden(); await expect(page.getByText('A-private-card-0', { exact: true })).toBeHidden(); expect((await state(page)).cards).toEqual([])
})

test('late due-card A read cannot replace B cards and is natively cancelled', async ({ page }) => {
  const held = gate(); let cancelled = false
  page.on('requestfailed', r => { if (r.url().includes('/flashcards/due?')) cancelled = true })
  await page.route('**/api/v1/flashcards/due?*', async r => { if (account(r.request()) !== 'A') return r.fulfill({ json: { cards: cards('B') } }); held.started = true; await held.promise; await r.fulfill({ json: { cards: cards('A') } }).catch(() => {}); held.ended = true })
  await page.goto('/flashcards'); await login(page, 'A'); await expect.poll(() => held.started).toBe(true); await switchB(page); await expect(page.getByText('B-private-card-0', { exact: true })).toBeVisible()
  held.release(); await expect.poll(() => held.ended).toBe(true); expect((await state(page)).cards[0].id).toBe('B-card-0'); expect(cancelled).toBe(true)
})

test('late A rating cannot advance or flip the current B card', async ({ page }) => {
  const held = gate()
  await page.route('**/api/v1/flashcards/review', async r => { held.started = true; await held.promise; await r.fulfill({ json: { next_review_at: '2026-10-10' } }).catch(() => {}); held.ended = true })
  await page.goto('/flashcards'); await login(page, 'A'); await page.getByText('A-private-card-0', { exact: true }).click(); await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).click(); await expect.poll(() => held.started).toBe(true)
  await switchB(page); await expect(page.getByText('B-private-card-0', { exact: true })).toBeVisible(); held.release(); await expect.poll(() => held.ended).toBe(true)
  const s = await state(page); expect(s.index).toBe(0); expect(s.flipped).toBe(false); await expect(page.getByText('B-private-card-0', { exact: true })).toBeVisible()
})

test('leaving pending due read cancels it without repopulating the global cards', async ({ page }) => {
  const held = gate(); let cancelled = false
  page.on('requestfailed', r => { if (r.url().includes('/flashcards/due?')) cancelled = true })
  await page.route('**/api/v1/flashcards/due?*', async r => { held.started = true; await held.promise; await r.fulfill({ json: { cards: cards('A') } }).catch(() => {}); held.ended = true })
  await page.goto('/flashcards'); await login(page, 'A'); await expect.poll(() => held.started).toBe(true); await page.getByRole('link', { name: '我的资料', exact: true }).click(); await expect(page.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible(); held.release(); await expect.poll(() => held.ended).toBe(true)
  expect((await state(page)).cards).toEqual([]); expect(cancelled).toBe(true)
})

test('real A recitation text and input cannot survive B login', async ({ page }) => {
  await page.goto('/recitation'); await login(page, 'A'); await startPassage(page, 'A', 'dictation'); await page.getByPlaceholder('在此输入你的默写内容...').fill('A秘密输入')
  await switchB(page); await expect(page.getByText('A-private-passage', { exact: true })).toBeHidden(); await expect(page.getByText('B-private-passage', { exact: true })).toBeVisible(); expect((await state(page)).input).toBe('')
})

test('late A hide plan cannot replace the selected B passage plan', async ({ page }) => {
  const held = gate()
  await page.route('**/api/v1/recitation/hide-plan', async r => { if (!r.request().postDataJSON().content.startsWith('A')) return r.fulfill({ json: plan('B') }); held.started = true; await held.promise; await r.fulfill({ json: plan('A') }).catch(() => {}); held.ended = true })
  await page.goto('/recitation'); await login(page, 'A'); await startPassage(page, 'A', 'hide'); await expect.poll(() => held.started).toBe(true)
  await switchB(page); await startPassage(page, 'B', 'hide'); await expect(page.getByText('B账号遮字', { exact: true })).toBeVisible(); held.release(); await expect.poll(() => held.ended).toBe(true)
  expect((await state(page)).hidePlan.segments[0].text).toBe('B账号遮字'); await expect(page.getByText('A账号遮字', { exact: true })).toBeHidden()
})

test('late A dictation result cannot enter B practice', async ({ page }) => {
  const held = gate()
  await page.route('**/api/v1/recitation/dictation/submit', async r => { held.started = true; await held.promise; await r.fulfill({ json: dictation('A') }).catch(() => {}); held.ended = true })
  await page.goto('/recitation'); await login(page, 'A'); await startPassage(page, 'A', 'dictation'); await page.getByPlaceholder('在此输入你的默写内容...').fill('A账号原文'); await page.getByRole('button', { name: /^检\s*查$/ }).click(); await expect.poll(() => held.started).toBe(true)
  await switchB(page); await startPassage(page, 'B', 'dictation'); held.release(); await expect.poll(() => held.ended).toBe(true)
  const s = await state(page); expect(s.passage.id).toBe('B-passage'); expect(s.dictationResult).toBeNull(); await expect(page.getByPlaceholder('在此输入你的默写内容...')).toBeVisible()
})

test('same-account positive controls keep actual quiz score/flash rating/dictation functional', async ({ page }) => {
  await page.route('**/api/v1/quiz/exam/submit', r => r.fulfill({ json: score('A') }))
  await page.goto('/quiz'); await login(page, 'A'); await startExam(page, 'A'); await page.getByRole('radio', { name: /正确/ }).check(); await page.getByRole('button', { name: /提交 \(1\/1\)/ }).click(); await expect.poll(async () => (await state(page)).results?.percentage).toBe(100)
  await page.getByRole('link', { name: '统一复习', exact: true }).click()
  await page.route('**/api/v1/flashcards/review', r => r.fulfill({ json: { next_review_at: '2026-10-10' } }))
  await page.getByText('A-private-card-0', { exact: true }).click(); await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).click(); await expect(page.getByText('A-private-card-1', { exact: true })).toBeVisible()
  await page.route('**/api/v1/recitation/dictation/submit', r => r.fulfill({ json: dictation('A') }))
  await page.getByRole('menuitem', { name: '更多工具', exact: true }).click(); await page.getByRole('link', { name: '背诵练习', exact: true }).click(); await startPassage(page, 'A', 'dictation'); await page.getByPlaceholder('在此输入你的默写内容...').fill('A账号原文'); await page.getByRole('button', { name: /^检\s*查$/ }).click(); await expect.poll(async () => (await state(page)).dictationResult?.accuracy).toBe(1)
})

test('pending flash rating accepts one real POST even with duplicate clicks', async ({ page }) => {
  const held = gate(); let writes = 0
  await page.route('**/api/v1/flashcards/review', async r => { writes++; held.started = true; await held.promise; await r.fulfill({ json: { next_review_at: '2026-10-10' } }).catch(() => {}); held.ended = true })
  await page.goto('/flashcards'); await login(page, 'A'); await page.getByText('A-private-card-0', { exact: true }).click()
  await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).evaluate((el: HTMLButtonElement) => { el.click(); el.click() })
  await expect.poll(() => writes).toBeGreaterThan(0); expect(writes).toBe(1); held.release(); await expect(page.getByText('A-private-card-1', { exact: true })).toBeVisible()
})

test('pending exam submission accepts one real POST even with duplicate clicks', async ({ page }) => {
  const held = gate(); let writes = 0
  await page.route('**/api/v1/quiz/exam/submit', async r => { writes++; held.started = true; await held.promise; await r.fulfill({ json: score('A') }).catch(() => {}); held.ended = true })
  await page.goto('/quiz'); await login(page, 'A'); await startExam(page, 'A'); await page.getByRole('radio', { name: /正确/ }).check()
  await page.getByRole('button', { name: /提交 \(1\/1\)/ }).evaluate((el: HTMLButtonElement) => { el.click(); el.click() })
  await expect.poll(() => writes).toBeGreaterThan(0); expect(writes).toBe(1); held.release(); await expect.poll(async () => (await state(page)).results?.percentage).toBe(100)
})

test('leaving recitation cancels the actual passage-list GET through optional Axios config', async ({ page }) => {
  const held = gate(); let cancelled = false
  page.on('requestfailed', r => { if (r.url().includes('/recitation/passages?')) cancelled = true })
  await page.route('**/api/v1/recitation/passages?*', async r => { held.started = true; await held.promise; await r.fulfill({ json: { passages: [passage('A')] } }).catch(() => {}); held.ended = true })
  try {
    await page.goto('/recitation'); await login(page, 'A'); await expect.poll(() => held.started).toBe(true)
    await page.getByRole('link', { name: '我的资料', exact: true }).click()
    await expect(page.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible()
    await expect.poll(() => cancelled).toBe(true)
  } finally { held.release() }
})
