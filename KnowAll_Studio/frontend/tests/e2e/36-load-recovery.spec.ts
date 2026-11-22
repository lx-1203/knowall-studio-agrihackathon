import { test, expect, E2E_TOKEN } from './fixtures'
import { summaryData } from './summary-navigation-fixture'
const coverage = { total_knowledge_points: 8, covered_by_questions: 4, covered_by_flashcards: 2, full_coverage: 2, full_coverage_rate: .25, coverage_rate_questions: .5, coverage_rate_flashcards: .25, uncovered_points: [], weak_points: [] }
const quiz = { session_id: 'session', questions: [{ id: 'q', question_text: '恢复后的真实题目', question_type: 'single_choice', difficulty: 'easy', difficulty_score: .2, options: [{ label: 'A', text: '正确选项' }, { label: 'B', text: '另一个选项' }], knowledge_points: [] }] }
test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 950 })
  await page.route('**/api/v1/knowledge/summary/recover', r => r.fulfill({ json: summaryData('recover', '# 保留当前总纲') }))
  await page.route('**/api/v1/knowledge/coverage/recover', r => r.fulfill({ json: coverage }))
  await page.route('**/api/v1/memory/stats', r => r.fulfill({ json: null }))
})

test('summary service failure has persistent retry, not a missing-summary claim', async ({ page }) => {
  let ready = false; let count = 0
  await page.route('**/api/v1/knowledge/summary/recover', r => { count++; return ready ? r.fulfill({ json: summaryData('recover', '# 恢复总纲') }) : r.fulfill({ status: 503, json: { detail: 'INTERNAL_TRACE_must_not_show' } }) })
  await page.goto('/summary/recover'); await expect(page.getByText('知识总纲加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('知识总纲未找到', { exact: true })).toBeHidden(); await expect(page.getByText(/INTERNAL_TRACE/)).toBeHidden()
  expect(count).toBe(3); ready = true
  await page.getByRole('button', { name: '重新加载', exact: true }).click()
  await expect(page.locator('.markdown-body')).toContainText('恢复总纲'); expect(count).toBe(4)
})

test('summary 404 and 403 explain missing content or access without claiming no generation', async ({ page }) => {
  for (const [status, text] of [[404, '该内容不存在或已被移除。'], [403, '当前账号无权访问这份内容。']] as const) {
    await page.route('**/api/v1/knowledge/summary/recover', r => r.fulfill({ status, json: {} }))
    await page.goto('/summary/recover'); await expect(page.getByText(text, { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '返回资料库', exact: true })).toBeVisible()
    await page.unroute('**/api/v1/knowledge/summary/recover')
  }
})

test('summary coverage failure retains source and reloads only coverage', async ({ page }) => {
  let ready = false; let summaryCount = 0; let writes = 0
  await page.route('**/api/v1/knowledge/summary/recover', r => { summaryCount++; return r.fulfill({ json: summaryData('recover', '# 保留当前总纲') }) })
  await page.route('**/api/v1/knowledge/coverage/recover', r => ready ? r.fulfill({ json: coverage }) : r.fulfill({ status: 503, json: {} }))
  page.on('request', r => { if (r.method() === 'POST' && /knowledge|agents|quiz/.test(new URL(r.url()).pathname)) writes++ })
  await page.goto('/summary/recover'); await expect(page.locator('.markdown-body')).toContainText('保留当前总纲')
  await page.getByRole('tab', { name: /覆盖率$/ }).click(); await expect(page.getByText('覆盖率加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('加载覆盖率中...', { exact: true })).toBeHidden(); ready = true
  await page.getByRole('button', { name: '重新加载覆盖率', exact: true }).click(); await expect(page.getByText('25%', { exact: true })).toBeVisible()
  expect(summaryCount).toBe(1); expect(writes).toBe(0)
})

test('successful generation with failed preview does not become an empty question bank or regenerate', async ({ page }) => {
  let ready = false; let generates = 0; const requests: any[] = []
  await page.route('**/api/v1/agents/orchestrate', r => { generates++; return r.fulfill({ json: { results: { question_bank: { status: 'success' }, study_plan: { status: 'skipped' }, mindmap: { status: 'skipped' }, language: { status: 'skipped' } } } }) })
  await page.route('**/api/v1/quiz/interactive/start?**', r => {
    requests.push({ url: r.request().url(), token: r.request().headers().authorization })
    return ready ? r.fulfill({ json: quiz }) : r.fulfill({ status: 503, json: {} })
  })
  await page.goto('/summary/recover'); await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await expect(page.getByText('题库生成: 完成', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: /题库 \(\?\)$/ }).click()
  await expect(page.getByText('题库预览加载失败', { exact: true })).toBeVisible(); await expect(page.getByText('尚未生成题目', { exact: true })).toBeHidden()
  expect(requests).toHaveLength(1); ready = true
  await page.getByRole('button', { name: '重新加载题库预览', exact: true }).click(); await expect(page.getByText('恢复后的真实题目', { exact: true })).toBeVisible()
  expect(generates).toBe(1); expect(requests).toHaveLength(2)
  for (const req of requests) { expect(req.token).toBe(`Bearer ${E2E_TOKEN}`); expect(new URL(req.url).searchParams.get('summary_id')).toBe('recover'); expect(new URL(req.url).searchParams.get('count')).toBe('5') }
  await page.screenshot({ path: '../docs/open-source-integration/round-13-preview-recovered.png' })
})

test('summary successful empty coverage and memory stop loading honestly', async ({ page }) => {
  await page.route('**/api/v1/knowledge/coverage/recover', r => r.fulfill({ json: null }))
  await page.goto('/summary/recover'); await expect(page.locator('.markdown-body')).toContainText('保留当前总纲')
  await page.getByRole('tab', { name: /覆盖率$/ }).click(); await expect(page.getByText('暂无覆盖率报告', { exact: true })).toBeVisible()
  await expect(page.getByText('加载覆盖率中...', { exact: true })).toBeHidden()
  await page.getByRole('tab', { name: /记忆卡$/ }).click(); await expect(page.getByText('暂无记忆卡统计', { exact: true })).toBeVisible()
})

test('summary memory and generated mindmap errors have their own recovery', async ({ page }) => {
  let ready = false
  await page.route('**/api/v1/memory/stats', r => ready ? r.fulfill({ json: { total_cards: 12, due_today: 2, average_accuracy: .8, review_queue_count: 1 } }) : r.fulfill({ status: 503, json: {} }))
  await page.route('**/api/v1/agents/orchestrate', r => r.fulfill({ json: { results: { mindmap: { status: 'success' } } } }))
  await page.route('**/api/v1/knowledge/summary/recover/mindmap', r => ready ? r.fulfill({ json: { nodes: [{ id: 'node' }], edges: [] } }) : r.fulfill({ status: 503, json: {} }))
  await page.goto('/summary/recover'); await page.getByRole('tab', { name: /记忆卡$/ }).click(); await expect(page.getByText('记忆卡统计加载失败', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await page.getByRole('tab', { name: /思维导图$/ }).click(); await expect(page.getByText('思维导图预览加载失败', { exact: true })).toBeVisible()
  ready = true; await page.getByRole('button', { name: '重新加载思维导图预览', exact: true }).click(); await expect(page.getByText('节点数', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: /记忆卡$/ }).click(); await page.getByRole('button', { name: '重新加载记忆卡统计', exact: true }).click(); await expect(page.getByText('总卡片数', { exact: true })).toBeVisible()
})

test('coverage page service failure is recoverable without triggering generation writes', async ({ page }) => {
  let ready = false; let writes = 0
  page.on('request', r => { if (r.method() === 'POST' && /knowledge|agents|quiz/.test(new URL(r.url()).pathname)) writes++ })
  await page.route('**/api/v1/knowledge/coverage/recover', r => ready ? r.fulfill({ json: coverage }) : r.fulfill({ status: 503, json: {} }))
  await page.goto('/coverage/recover'); await expect(page.getByText('覆盖率报告加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('该知识总纲尚未生成覆盖率报告', { exact: true })).toBeHidden()
  ready = true; await page.getByRole('button', { name: '重新加载', exact: true }).click(); await expect(page.getByRole('heading', { name: '覆盖率报告', exact: true })).toBeVisible(); expect(writes).toBe(0)
})

test('coverage successful null remains a genuine no-report state', async ({ page }) => {
  await page.route('**/api/v1/knowledge/coverage/recover', r => r.fulfill({ json: null }))
  await page.goto('/coverage/recover'); await expect(page.getByText('该知识总纲尚未生成覆盖率报告', { exact: true })).toBeVisible()
  await expect(page.getByText('覆盖率报告加载失败', { exact: true })).toBeHidden()
})

test('interactive quiz failure is recoverable and each explicit attempt starts only one session', async ({ page }) => {
  let ready = false; const counts: string[] = []
  await page.route('**/api/v1/quiz/interactive/start?**', r => { counts.push(new URL(r.request().url()).searchParams.get('count')!); return ready ? r.fulfill({ json: quiz }) : r.fulfill({ status: 503, json: {} }) })
  await page.goto('/quiz/interactive/recover'); await expect(page.getByText('题目加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('暂无可用题目', { exact: true })).toBeHidden(); expect(counts).toEqual(['20'])
  ready = true; await page.getByRole('button', { name: '重新加载', exact: true }).click(); await expect(page.getByText('恢复后的真实题目', { exact: true })).toBeVisible(); expect(counts).toEqual(['20', '20'])
})

test('interactive successful empty is not a failure', async ({ page }) => {
  await page.route('**/api/v1/quiz/interactive/start?**', r => r.fulfill({ json: { session_id: '', questions: [] } }))
  await page.goto('/quiz/interactive/recover'); await expect(page.getByText('暂无可用题目', { exact: true })).toBeVisible(); await expect(page.getByText('题目加载失败', { exact: true })).toBeHidden()
})

test('mindmap failure has native recovery, successful null is not an exception', async ({ page }) => {
  let ready = false
  await page.route('**/api/v1/knowledge/summary/recover/mindmap', r => ready ? r.fulfill({ json: null }) : r.fulfill({ status: 503, json: {} }))
  await page.goto('/mindmap/recover'); await expect(page.getByText('思维导图加载失败', { exact: true })).toBeVisible()
  ready = true; await page.getByRole('button', { name: '重新加载', exact: true }).click(); await expect(page.getByText('暂无思维导图', { exact: true })).toBeVisible(); await expect(page.getByText('思维导图加载失败', { exact: true })).toBeHidden()
})

test('read 401 still exits to login instead of leaving an empty-data page', async ({ page }) => {
  await page.route('**/api/v1/knowledge/coverage/recover', r => r.fulfill({ status: 401, json: {} }))
  await page.goto('/coverage/recover'); await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('knowall_token'))).toBeNull()
})

test('failed generation does not strand an earlier successful coverage read in loading', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/knowledge/coverage/recover', async r => { await gate; await r.fulfill({ json: coverage }) })
  await page.route('**/api/v1/agents/orchestrate', r => r.fulfill({ status: 503, json: { detail: 'generation unavailable' } }))
  await page.goto('/summary/recover'); await expect(page.locator('.markdown-body')).toContainText('保留当前总纲')
  await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await expect(page.getByText('生成失败: generation unavailable', { exact: true })).toBeVisible()
  release(); await page.getByRole('tab', { name: /覆盖率$/ }).click()
  await expect(page.getByText('25%', { exact: true })).toBeVisible()
  await expect(page.getByText('加载覆盖率中...', { exact: true })).toBeHidden()
})

test('rapid explicit preview retry creates one legacy session', async ({ page }) => {
  let ready = false; let count = 0
  await page.route('**/api/v1/agents/orchestrate', r => r.fulfill({ json: { results: { question_bank: { status: 'success' } } } }))
  await page.route('**/api/v1/quiz/interactive/start?**', async r => {
    count++
    if (!ready) return r.fulfill({ status: 503, json: {} })
    await new Promise(resolve => setTimeout(resolve, 300)); await r.fulfill({ json: quiz })
  })
  await page.goto('/summary/recover'); await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await page.getByRole('tab', { name: /题库 \(\?\)$/ }).click(); await expect(page.getByText('题库预览加载失败', { exact: true })).toBeVisible()
  ready = true; await page.getByRole('button', { name: '重新加载题库预览', exact: true }).evaluate((el: HTMLElement) => { el.click(); el.click() })
  await expect(page.getByText('恢复后的真实题目', { exact: true })).toBeVisible(); expect(count).toBe(2)
})

test('failed restart retains completed answers and retry replaces them only after success', async ({ page }) => {
  let ready = true
  await page.route('**/api/v1/quiz/interactive/start?**', r => ready ? r.fulfill({ json: quiz }) : r.fulfill({ status: 503, json: {} }))
  await page.route('**/api/v1/quiz/interactive/submit', r => r.fulfill({ json: { is_correct: true, correct_answer: 'A', analysis: '原答案解析' } }))
  await page.goto('/quiz/interactive/recover'); await page.getByRole('radio', { name: /正确选项/ }).click()
  await expect(page.getByText('答题完成！', { exact: true })).toBeVisible()
  ready = false; await page.getByRole('button', { name: /重新开始$/ }).click()
  await expect(page.getByText('题目加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('正确率: 100.0% (1/1)', { exact: true })).toBeVisible()
  await expect(page.getByText('答题完成！', { exact: true })).toBeVisible()
  ready = true; await page.getByRole('button', { name: '重新加载', exact: true }).click()
  await expect(page.getByText('答题完成！', { exact: true })).toBeHidden(); await expect(page.getByRole('radio', { name: /正确选项/ })).toBeEnabled()
})

for (const [pagePath, apiPath] of [['/coverage/recover', '/knowledge/coverage/recover'], ['/mindmap/recover', '/knowledge/summary/recover/mindmap'], ['/quiz/interactive/recover', '/quiz/interactive/start?']] as const) {
  test(`leaving ${pagePath} aborts its pending read without an error`, async ({ page }) => {
    let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let started = false; let aborted = false
    page.on('requestfailed', r => { if (r.url().includes(apiPath)) aborted = true })
    await page.route(`**/api/v1${apiPath}**`, async r => { started = true; await gate; await r.fulfill({ json: null }).catch(() => {}) })
    await page.goto(pagePath); await expect.poll(() => started).toBe(true)
    await page.evaluate(() => { history.pushState(null, '', '/summary/recover'); dispatchEvent(new PopStateEvent('popstate')) })
    await expect(page.locator('.markdown-body')).toContainText('保留当前总纲'); await expect.poll(() => aborted).toBe(true)
    release(); await expect(page.getByText(/报告加载失败|题目加载失败|思维导图加载失败/)).toBeHidden()
  })
}

test('network error supports keyboard recovery and error controls fit a narrow dark viewport', async ({ page }) => {
  let ready = false
  await page.route('**/api/v1/knowledge/summary/recover', r => ready ? r.fulfill({ json: summaryData('recover', '# 键盘恢复原文') }) : r.abort('failed'))
  await page.goto('/summary/recover'); await expect(page.getByText('知识总纲加载失败', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('button', { name: '切换到深色模式', exact: true }).click()
  const button = page.getByRole('button', { name: '重新加载', exact: true }); const box = await button.boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '../docs/open-source-integration/round-13-narrow-dark-error.png', animations: 'disabled' })
  ready = true; await button.focus(); await page.keyboard.press('Enter'); await expect(page.locator('.markdown-body')).toContainText('键盘恢复原文')
})
