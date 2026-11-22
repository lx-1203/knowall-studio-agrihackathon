import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'
import fs from 'node:fs'
const evidencePrefix = process.env.OSS_EVIDENCE_PREFIX || 'round-09'
import path from 'node:path'

const lesson = '学习 $x^2$，价格 \\$5 和 \\$10。\n\n$$\n\\frac{1}{2}\n$$\n\n```python\ndef learn(value):\n    return value + 1\n```\n\n`$raw$`\n\n|知识|进度|\n|---|---|\n|数学|50%|'
const analysis = '反馈 $y^2$\n\n```js\nconst score = 0.5;\n```'
const question = { id: 'practice-q', question_type: 'fill_blank', difficulty: 'medium', difficulty_score: 0.5,
  cognitive_level: 'L2_understand', tags: [], question_text: lesson, options: [], answer: '1;2', analysis }
async function show(page: Page, text: string) {
  await page.evaluate(async text => (await import('/.tmp/practiceHarness.tsx')).show(text), text)
}
async function exam(page: Page) {
  await page.goto('/quiz')
  await page.evaluate(async q => { const { useQuizStore } = await import('/src/stores/index.ts'); useQuizStore.getState().setCurrentExam({ paper_id: 'practice-exam', title: '原生练习', question_count: 1, questions: [q] }) }, question)
  await page.getByRole('tab', { name: /答题$/ }).click()
}

test('real quiz question uses native math, escaped currency, GFM and code without changing its input', async ({ page }) => {
  await exam(page)
  const q = page.locator('#question-practice-q')
  await expect(q.locator('.katex')).toHaveCount(2)
  await expect(q.locator('.katex-display')).toHaveCount(1)
  await expect(q).toContainText('价格 $5 和 $10')
  await expect(q.locator('p > code')).toHaveText('$raw$')
  await expect(q.locator('table td')).toHaveText(['数学', '50%'])
  await expect(q.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
  await expect(q.getByPlaceholder('请输入答案...', { exact: true })).toBeEnabled()
  await q.screenshot({ path: path.resolve(`../docs/open-source-integration/${evidencePrefix}-practice-question.png`) })
})

test('real exam submit keeps original answer/Bearer and partial score with native feedback and review', async ({ page }) => {
  const sent: object[] = []
  await page.route('**/api/v1/quiz/exam/submit', r => {
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`); sent.push(r.request().postDataJSON())
    return r.fulfill({ json: { total: 1, correct: 0, score: 2.5, percentage: 50, details: [{ question_id: question.id, is_correct: false, correct_answer: '1;2', user_answer: '1', partial_score: 0.5, analysis }] } })
  })
  await exam(page); const q = page.locator('#question-practice-q')
  await q.getByPlaceholder('请输入答案...', { exact: true }).fill('1')
  await page.getByRole('button', { name: /^提交 \(/ }).click()
  await expect(q).toContainText('部分得分: 50%')
  await expect(q.locator('annotation').last()).toHaveText('y^2')
  await expect(q.locator('pre code').last()).toHaveText('const score = 0.5;\n')
  expect(sent).toEqual([{ paper_id: 'practice-exam', answers: { 'practice-q': '1' } }])
  await page.getByText('答题回顾', { exact: true }).click()
  const recap = page.locator('#review-practice-q')
  await expect(recap.locator('.katex')).toHaveCount(3)
  await expect(recap.locator('pre code').last()).toHaveText('const score = 0.5;\n')
  await expect(recap).toContainText('你的答案'); await expect(recap).toContainText('1;2')
})

test('real interactive question and completed recap render native content while option submission stays literal', async ({ page }) => {
  const q = { ...question, question_type: 'single_choice', options: [{ label: 'A', text: 'first' }, { label: 'B', text: 'second' }] }
  let answer: object | undefined
  await page.route('**/api/v1/quiz/interactive/start**', r => r.fulfill({ json: { session_id: 'practice-session', questions: [q] } }))
  await page.route('**/api/v1/quiz/interactive/submit', r => {
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`); answer = r.request().postDataJSON()
    return r.fulfill({ json: { is_correct: false, correct_answer: 'B', analysis } })
  })
  await page.goto('/quiz/interactive/practice-summary')
  await expect(page.locator('.katex')).toHaveCount(2)
  await page.getByRole('radio', { name: 'A. first', exact: true }).check()
  await expect(page.getByText('答题完成！', { exact: true })).toBeVisible()
  expect(answer).toMatchObject({ question_id: q.id, user_answer: 'A', session_id: 'practice-session' })
  await expect(page.locator('annotation').last()).toHaveText('y^2')
  await expect(page.locator('pre code').last()).toHaveText('const score = 0.5;\n')
})

test('compatibility RichText respects inline code, escaped dollars and native fenced code', async ({ page }) => {
  await page.goto('/dashboard'); await show(page, lesson)
  const content = page.locator('#practice-test-mount')
  await expect(content).toContainText('价格 $5 和 $10')
  await expect(content.locator('p > code')).toHaveText('$raw$')
  await expect(content.locator('.katex')).toHaveCount(2)
  await expect(content.locator('pre code')).toHaveText('def learn(value):\n    return value + 1\n')
})

test('untrusted question commands cannot add active HTML/classes/links or automatic image requests', async ({ page }) => {
  const requests: string[] = []; page.on('request', r => requests.push(r.url()))
  await page.goto('/dashboard')
  const source = '$\\htmlClass{owned}{x}$\n\n$\\href{https://tracker.example.test}{remote}$\n\n[危险](javascript:alert(1))\n\n<img src=x onerror="window.practiceExecuted=1">\n<script>window.practiceExecuted=2</script>\n\n![题目图片](https://tracker.example.test/question.png)'
  await show(page, source); const content = page.locator('#practice-test-mount')
  await expect(content.locator('.katex')).toHaveCount(2)
  await expect(content.locator('.owned, script, img, a[href^="javascript:"], .katex a')).toHaveCount(0)
  await expect(content).toContainText('题目图片')
  expect(await page.evaluate(() => (window as unknown as { practiceExecuted?: number }).practiceExecuted)).toBeUndefined()
  expect(requests.some(r => r.startsWith('https://tracker.example.test'))).toBe(false)
  fs.writeFileSync(path.resolve(`../docs/open-source-integration/${evidencePrefix}-untrusted-requests.json`), JSON.stringify(requests, null, 2))
})

test('bad formula stays readable and rapid replacement does not retain previous math', async ({ page }) => {
  await page.goto('/dashboard'); await show(page, '$\\frac{$')
  await expect(page.locator('#practice-test-mount .katex-error')).toContainText('\\frac{')
  await show(page, '$x^2$'); await show(page, '当前纯文字')
  await expect(page.locator('#practice-test-mount')).toHaveText('当前纯文字')
  await expect(page.locator('#practice-test-mount .katex')).toHaveCount(0)
})

test('shared renderer follows dark syntax theme and confines long formulas/code on narrow widths', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('knowall-theme', 'dark'))
  await page.goto('/dashboard'); await show(page, lesson + '\n\n```text\n' + 'long'.repeat(150) + '\n```')
  const content = page.locator('#practice-test-mount')
  await expect(content.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(86, 156, 214)')
  await expect(content.locator('pre').last()).toHaveCSS('overflow-x', 'auto')
  expect(await content.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  await content.screenshot({ path: path.resolve(`../docs/open-source-integration/${evidencePrefix}-practice-dark.png`) })
})

test('failed native renderer chunk leaves the complete original and answer controls usable', async ({ page }) => {
  let blocked = 0
  await page.route('**/src/components/MarkdownContent.tsx*', route => { blocked++; return route.abort('failed') })
  await exam(page)
  const q = page.locator('#question-practice-q')
  await expect(q.locator('.learning-content-fallback')).toHaveText(lesson)
  expect(blocked).toBeGreaterThan(0)
  await q.getByPlaceholder('请输入答案...', { exact: true }).fill('仍可作答')
  await expect(q.getByPlaceholder('请输入答案...', { exact: true })).toHaveValue('仍可作答')
  await expect(page.getByRole('button', { name: /^提交 \(/ })).toBeEnabled()
})

test('empty quiz does not request the shared native content module before a question renders', async ({ page }) => {
  const requests: string[] = []; page.on('request', r => requests.push(r.url()))
  await page.goto('/quiz'); await page.getByRole('tab', { name: /答题$/ }).waitFor()
  expect(requests.some(url => url.includes('/src/components/MarkdownContent.tsx'))).toBe(false)
  await page.evaluate(async q => { const { useQuizStore } = await import('/src/stores/index.ts'); useQuizStore.getState().setCurrentExam({ paper_id: 'practice-exam', title: '原生练习', question_count: 1, questions: [q] }) }, question)
  await page.getByRole('tab', { name: /答题$/ }).click()
  await expect(page.locator('#question-practice-q .katex')).toHaveCount(2)
  expect(requests.some(url => url.includes('/src/components/MarkdownContent.tsx'))).toBe(true)
  fs.writeFileSync(path.resolve(`../docs/open-source-integration/${evidencePrefix}-lazy-requests.json`), JSON.stringify(requests, null, 2))
})

test('existing document chunk reader uses the native bridge and keeps text selection/search authorized', async ({ page }) => {
  await page.route('**/api/v1/documents/practice-doc/chunks', route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    return route.fulfill({ json: [{ id: 'practice-chunk', index: 0, token_count: 20, page_range: 'p1', text: '可选择的学习原文\n\n' + lesson }] })
  })
  await page.goto('/dashboard')
  await page.evaluate(async () => (await import('/.tmp/practiceHarness.tsx')).showDocument())
  const content = page.locator('#practice-test-mount')
  await expect(content.locator('.katex')).toHaveCount(2)
  await expect(content.locator('pre code')).toHaveText('def learn(value):\n    return value + 1\n')
  await content.locator('p').first().evaluate(el => {
    const selection = window.getSelection()!, range = document.createRange()
    range.selectNodeContents(el); selection.removeAllRanges(); selection.addRange(range)
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  })
  await content.getByRole('button', { name: /搜索/ }).click()
  expect(await page.evaluate(() => (window as unknown as { practiceSearch: string }).practiceSearch)).toBe('可选择的学习原文')
})
