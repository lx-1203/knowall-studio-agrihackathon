import { type Page } from '@playwright/test'
import { test, expect, stubBackend } from './fixtures'
const startPath = '**/api/v1/quiz/interactive/start?**'
const submitPath = '**/api/v1/quiz/interactive/submit'
const question = (id: string, kind = 'single_choice') => ({ id, question_type: kind, question_text: `${id}-题目`, options: [{ label: 'A', text: '甲' }, { label: 'B', text: '乙' }], difficulty: 'medium', tags: [] })
const quiz = (id: string, kind = 'single_choice', count = 1) => ({ session_id: `${id}-session`, questions: Array.from({ length: count }, (_, i) => question(`${id}-${i + 1}`, kind)), total: count })
const score = { is_correct: true, correct_answer: 'A', analysis: '当前评分' }
const go = async (page: Page, id: string) => page.evaluate(id => { history.pushState(null, '', `/quiz/interactive/${id}`); dispatchEvent(new PopStateEvent('popstate')) }, id)
const switchUser = async (page: Page, id: string) => page.evaluate(async id => {
  const { useAuthStore } = await import('/src/stores/index.ts')
  useAuthStore.getState().login(`${id}-token`, { id, username: id, email: `${id}@fixture.invalid` })
}, id)
test.beforeEach(async ({ page }) => { await stubBackend(page) })

for (const kind of ['single_choice', 'true_false', 'multi_choice', 'fill_blank']) {
  for (const failure of ['503', 'network']) {
    test(`${kind} keeps input and explicitly retries after ${failure}`, async ({ page }) => {
      let calls = 0
      await page.route(startPath, r => r.fulfill({ json: quiz('retry', kind) }))
      await page.route(submitPath, r => ++calls === 1 ? (failure === '503' ? r.fulfill({ status: 503, json: { detail: '明确模拟提交故障' } }) : r.abort('failed')) : r.fulfill({ json: score }))
      await page.goto('/quiz/interactive/retry'); await expect(page.getByText('retry-1-题目', { exact: true })).toBeVisible()
      if (kind === 'multi_choice') { await page.getByText('A. 甲', { exact: true }).click(); await expect(page.getByRole('checkbox', { name: 'A. 甲' })).toBeChecked(); await page.getByRole('button', { name: /提交答案$/ }).click() }
      else if (kind === 'fill_blank') { await page.getByPlaceholder('请输入你的答案...').fill('保留输入'); await page.getByRole('button', { name: /提交答案$/ }).click() }
      else await page.getByRole('radio', { name: 'A. 甲' }).check()
      await expect(page.getByText(/提交失败:/)).toBeVisible()
      if (kind === 'multi_choice') { await expect(page.getByRole('checkbox', { name: 'A. 甲' })).toBeChecked(); await expect(page.getByRole('checkbox', { name: 'A. 甲' })).toBeEnabled() }
      if (kind === 'fill_blank') { await expect(page.getByPlaceholder('请输入你的答案...')).toHaveValue('保留输入'); await expect(page.getByPlaceholder('请输入你的答案...')).toBeEnabled() }
      await page.getByRole('button', { name: ['single_choice', 'true_false'].includes(kind) ? '重新提交' : /提交答案$/ }).click()
      await expect(page.getByText('答题完成！', { exact: true })).toBeVisible(); expect(calls).toBe(2)
    })
  }
}

test('same-turn manual double click makes one request', async ({ page }) => {
  let calls = 0; let release!: () => void; const gate = new Promise<void>(r => { release = r })
  await page.route(startPath, r => r.fulfill({ json: quiz('double', 'fill_blank') }))
  await page.route(submitPath, async r => { calls++; await gate; await r.fulfill({ json: score }).catch(() => {}) })
  await page.goto('/quiz/interactive/double'); await page.getByPlaceholder('请输入你的答案...').fill('甲')
  await page.getByRole('button', { name: /提交答案$/ }).evaluate((el: HTMLElement) => { el.click(); el.click() })
  await expect.poll(() => calls).toBe(1); release()
  await expect(page.getByText('答题完成！', { exact: true })).toBeVisible(); expect(calls).toBe(1)
})

test('answered question revisited through sheet stays graded and locked', async ({ page }) => {
  await page.clock.install()
  await page.route(startPath, r => r.fulfill({ json: quiz('review', 'single_choice', 3) }))
  await page.route(submitPath, r => r.fulfill({ json: score }))
  await page.goto('/quiz/interactive/review'); await page.getByRole('radio', { name: 'A. 甲' }).check()
  await expect(page.getByText('✅ 回答正确！', { exact: true })).toBeVisible(); await page.clock.runFor(1700)
  await expect(page.getByText('review-2-题目', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /答题卡/ }).click(); await page.getByRole('button', { name: '1', exact: true }).click()
  await expect(page.getByText('✅ 回答正确！', { exact: true })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'A. 甲' })).toBeDisabled()
})

for (const phase of ['pending', 'timer']) {
  test(`sheet navigation during ${phase} is not overwritten by old auto advance`, async ({ page }) => {
    await page.clock.install(); let release!: () => void; const gate = new Promise<void>(r => { release = r }); let started = false
    await page.route(startPath, r => r.fulfill({ json: quiz('nav', 'single_choice', 3) }))
    await page.route(submitPath, async r => { started = true; if (phase === 'pending') await gate; await r.fulfill({ json: score }).catch(() => {}) })
    await page.goto('/quiz/interactive/nav'); await page.getByRole('radio', { name: 'A. 甲' }).check(); await expect.poll(() => started).toBe(true)
    if (phase === 'timer') await expect(page.getByText('✅ 回答正确！', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: /答题卡/ }).click(); await page.getByRole('button', { name: '3', exact: true }).click()
    release(); await page.clock.runFor(2000)
    await expect(page.getByText('nav-3-题目', { exact: true })).toBeVisible(); await expect(page.getByText('nav-2-题目', { exact: true })).toBeHidden()
  })
}

for (const outcome of ['success', 'failure']) {
  test(`summary switch ignores old submit ${outcome} and permits new submit`, async ({ page }) => {
    let release!: () => void; const gate = new Promise<void>(r => { release = r }); let started = false; let aborted = false
    page.on('requestfailed', r => { if (r.url().endsWith('/quiz/interactive/submit')) aborted = true })
    await page.route(startPath, r => r.fulfill({ json: quiz(new URL(r.request().url()).searchParams.get('summary_id')!) }))
    await page.route(submitPath, async r => {
      if (r.request().postDataJSON().session_id === 'old-session') { started = true; await gate; await r.fulfill({ status: outcome === 'success' ? 200 : 503, json: outcome === 'success' ? { ...score, analysis: '旧会话迟到评分' } : { detail: '旧会话迟到错误' } }).catch(() => {}) }
      else await r.fulfill({ json: score })
    })
    await page.goto('/quiz/interactive/old'); await page.getByRole('radio', { name: 'A. 甲' }).check(); await expect.poll(() => started).toBe(true)
    await go(page, 'new'); await expect(page.getByText('new-1-题目', { exact: true })).toBeVisible()
    await expect.poll(() => aborted).toBe(true); release()
    await expect(page.getByRole('radio', { name: 'A. 甲' })).toBeEnabled(); await page.getByRole('radio', { name: 'A. 甲' }).check()
    await expect(page.getByText('正确率: 100.0% (1/1)', { exact: true })).toBeVisible()
    await expect(page.getByText(/旧会话迟到评分|旧会话迟到错误/)).toBeHidden()
  })
}

for (const pending of ['start', 'submit']) {
  test(`in-place account change rejects pending ${pending} from old user`, async ({ page }) => {
    let release!: () => void; const gate = new Promise<void>(r => { release = r }); let started = false
    await page.addInitScript(() => { localStorage.setItem('knowall_token', 'A-token'); localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'A', email: 'A@fixture.invalid' })) })
    const account = (r: any) => r.request().headers().authorization?.includes('B-token') ? 'B' : 'A'
    await page.route(startPath, async r => { const who = account(r); if (who === 'A' && pending === 'start') { started = true; await gate }; await r.fulfill({ json: quiz(who) }).catch(() => {}) })
    await page.route(submitPath, async r => { if (account(r) === 'A') { started = true; await gate }; await r.fulfill({ json: score }).catch(() => {}) })
    await page.goto('/quiz/interactive/account')
    if (pending === 'submit') await page.getByRole('radio', { name: 'A. 甲' }).check()
    await expect.poll(() => started).toBe(true); await switchUser(page, 'B')
    await expect(page.getByText('A-1-题目', { exact: true })).toBeHidden(); await expect(page.getByText('B-1-题目', { exact: true })).toBeVisible()
    release(); await page.getByRole('radio', { name: 'A. 甲' }).check(); await expect(page.getByText('正确率: 100.0% (1/1)', { exact: true })).toBeVisible()
  })
}

test('leaving page aborts pending submit without late failure toast', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(r => { release = r }); let started = false; let aborted = false
  page.on('requestfailed', r => { if (r.url().endsWith('/quiz/interactive/submit')) aborted = true })
  await page.route(startPath, r => r.fulfill({ json: quiz('leave') }))
  await page.route(submitPath, async r => { started = true; await gate; await r.fulfill({ status: 503, json: { detail: '迟到离页错误' } }).catch(() => {}) })
  await page.goto('/quiz/interactive/leave'); await page.getByRole('radio', { name: 'A. 甲' }).check(); await expect.poll(() => started).toBe(true)
  await page.getByRole('link', { name: '全部题库', exact: true }).click(); await expect(page).toHaveURL(/\/quiz$/)
  await expect.poll(() => aborted).toBe(true); release(); await expect(page.getByText(/迟到离页错误/)).toBeHidden()
})

test('same account token refresh keeps pending answer and does not restart session', async ({ page }) => {
  let starts = 0; let submitted = false; let release!: () => void
  const gate = new Promise<void>(r => { release = r })
  await page.route(startPath, r => { starts++; return r.fulfill({ json: quiz('refresh', 'single_choice', 2) }) })
  await page.route(submitPath, async r => { submitted = true; await gate; await r.fulfill({ json: score }).catch(() => {}) })
  await page.goto('/quiz/interactive/refresh'); await page.getByRole('radio', { name: 'A. 甲' }).check()
  await expect.poll(() => submitted).toBe(true)
  await page.evaluate(async () => {
    const { useAuthStore } = await import('/src/stores/index.ts')
    const user = useAuthStore.getState().user!
    useAuthStore.getState().login('rotated-same-account-token', user)
  })
  await expect(page.getByText('refresh-1-题目', { exact: true })).toBeVisible(); expect(starts).toBe(1)
  release(); await expect(page.getByText('refresh-2-题目', { exact: true })).toBeVisible()
  await page.getByRole('radio', { name: 'A. 甲' }).check()
  await expect(page.getByText('正确率: 100.0% (2/2)', { exact: true })).toBeVisible(); expect(starts).toBe(1)
})



