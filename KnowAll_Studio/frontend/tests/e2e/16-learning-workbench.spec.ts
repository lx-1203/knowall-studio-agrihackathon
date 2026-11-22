import { test, expect } from './fixtures'

const pack = { id: 'pack1', title: '示例学习包', stale: false, coverage: { total_sections: 1, question_sections: 1, flashcard_sections: 1 }, quality: { total: 1, blocked: 0 }, sections: [{ id: 'section1', title: '第一章', notes: '章节笔记内容', chunk_ids: ['chunk1'], page_ranges: [1], errors: [], questions: [], flashcards: [] }] }

test('chapter practice requests fresh questions rather than only due review', async ({ page }) => {
  let mode = ''
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/session')) { mode = route.request().postDataJSON().mode; return route.fulfill({ json: { items: [] } }) }
    return route.fulfill({ json: path.endsWith('/pack') ? pack : { sections: [] } })
  })
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.getByRole('button', { name: '章节练习', exact: true }).click()
  await expect(page.getByText('本轮练习已完成')).toBeVisible()
  expect(mode).toBe('practice')
})

test('multiple choice generation sends the backend multi_choice enum', async ({ page }) => {
  let types: string[] = []
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/generate')) { types = route.request().postDataJSON().question_types; return route.fulfill({ json: pack }) }
    return route.fulfill({ json: path.endsWith('/pack') ? null : { sections: [] } })
  })
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.getByRole('combobox', { name: '题型' }).click()
  await page.locator('.ant-select-item-option-content').filter({ hasText: /^多选$/ }).click()
  await page.getByRole('heading', { name: '学习工作台' }).click()
  await page.getByRole('button', { name: '生成学习包', exact: true }).click()
  await expect(page.getByText('示例学习包')).toBeVisible()
  expect(types).toContain('multi_choice')
})

test('true false A/B options submit canonical boolean strings and show partial score', async ({ page }) => {
  let answer = ''
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/session')) return route.fulfill({ json: { items: [{ kind: 'question', title: '判断章节', notes: '笔记', question: { id: 'tf1', question_type: 'true_false', question_text: '请判断', options: [{ label: 'A', text: '正确' }, { label: 'B', text: '错误' }] }, source: {} }] } })
    if (path.endsWith('/answers')) { answer = route.request().postDataJSON().answer; return route.fulfill({ json: { is_correct: false, score_ratio: 0.5, correct_answer: 'true', analysis: '判断解析' } }) }
    return route.fulfill({ json: path.endsWith('/pack') ? pack : { sections: [] } })
  })
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.getByRole('button', { name: '章节练习', exact: true }).click()
  await page.getByRole('radio', { name: /正确/ }).check()
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('判断解析')).toBeVisible()
  expect(answer).toBe('true')
  await expect(page.getByText('得分：50%')).toBeVisible()
})

test('structured practice keeps answers private, retries failures, and rates cards after reveal', async ({ page }) => {
  let submissions = 0
  let rated = false
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: route.request().url().includes('/chunks') ? [{ id: 'chunk1', text: '定位原文内容' }, { id: 'chunk2', text: '无关章节原文' }] : [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/preview')) return route.fulfill({ json: { sections: [] } })
    if (url.pathname.endsWith('/pack')) return route.fulfill({ json: pack })
    if (url.pathname.endsWith('/session')) return route.fulfill({ json: { items: [
      { kind: 'question', reason: 'practice', title: '第一章', notes: '章节笔记内容', source: { chunk_ids: ['chunk1'], page_ranges: [1] }, question: { id: 'q1', question_type: 'matching', question_text: '匹配项目', options: [{ label: 'A', text: '项目一', choices: ['候选一', '候选二'] }] } },
      { kind: 'flashcard', reason: 'due', title: '第一章', notes: '章节笔记内容', source: {}, card: { id: 'card1', front: '卡片正面', back: '隐藏卡片答案' } },
    ] } })
    if (url.pathname.endsWith('/answers')) {
      submissions++
      expect(JSON.parse(route.request().postDataJSON().answer)).toEqual({ A: '候选一' })
      if (submissions === 1) return route.fulfill({ status: 400, json: { detail: 'temporary failure' } })
      return route.fulfill({ json: { is_correct: true, correct_answer: '反馈专属答案', analysis: '反馈解析', source: { chunk_ids: ['chunk1'], page_ranges: [1] } } })
    }
    if (url.pathname.endsWith('/review')) { rated = true; return route.fulfill({ json: { ok: true } }) }
    return route.fulfill({ json: [] })
  })
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '智能复习' }).click()
  await expect(page.getByText('章节笔记内容')).toBeVisible()
  await expect(page.getByText('反馈专属答案')).toHaveCount(0)
  await page.getByRole('combobox', { name: 'A 的答案' }).click()
  await page.getByRole('combobox', { name: 'A 的答案' }).press('Enter')
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('提交失败，答案已保留，请重试。')).toBeVisible()
  await expect(page.getByRole('button', { name: '下一项' })).toHaveCount(0)
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('正确答案：反馈专属答案')).toBeVisible()
  await page.getByRole('button', { name: '定位来源原文' }).click()
  await expect(page.getByText('定位原文内容')).toBeVisible()
  await expect(page.getByText('无关章节原文')).toHaveCount(0)
  await page.getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByRole('button', { name: '下一项' }).click()
  await expect(page.getByText('隐藏卡片答案')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '良好', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '显示答案' }).click()
  await expect(page.getByText('隐藏卡片答案')).toBeVisible()
  await page.screenshot({ path: '.tmp/learning-practice.png', fullPage: true })
  await page.getByRole('button', { name: '良好', exact: true }).click()
  await expect(page.getByText('本轮练习已完成')).toBeVisible()
  expect(rated).toBeTruthy()
})

test('section zero budgets are intentional while all blocked questions require repair', async ({ page }) => {
  const qualityPack = { ...pack, config: { question_count: 10, card_count: 10 }, sections: [
    { id: 'skip', title: '未分配章节', question_budget: 0, card_budget: 0, questions: [], flashcards: [], errors: [] },
    { id: 'blocked', title: '隔离章节', question_budget: 1, card_budget: 0, questions: [{ id: 'bad', question_text: '待修复题干', quality: { eligible: false, issues: ['答案无效'] } }], flashcards: [], errors: [] },
  ] }
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', route => route.fulfill({ json: route.request().url().includes('/pack') ? qualityPack : { sections: [] } }))
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.getByRole('tab', { name: '质检与缺漏' }).click()
  await expect(page.getByText('1 个章节存在生成失败或内容缺漏')).toBeVisible()
  await expect(page.getByText('隔离章节： 全部习题已隔离，请修复')).toBeVisible()
  await expect(page.getByText(/缺卡片/)).toHaveCount(0)
})

test('stale packs block practice and workbench fits a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', route => route.fulfill({ json: route.request().url().includes('/preview') ? { sections: [] } : { ...pack, stale: true } }))
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '智能复习' })).toBeDisabled()
  await expect(page.getByText('来源已更新，当前学习包已过期，请重新生成。')).toBeVisible()
  await expect(page.getByRole('button', { name: '生成学习包', exact: true })).toBeEnabled()
  await page.getByRole('heading', { name: '学习工作台' }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: '.tmp/learning-mobile.png', fullPage: true })
})

test('ordering supports keyboard-accessible move buttons and JSON submission', async ({ page }) => {
  let submitted: string[] = []
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'doc1', title: '示例文档' }] }))
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/preview')) return route.fulfill({ json: { sections: [] } })
    if (path.endsWith('/pack')) return route.fulfill({ json: pack })
    if (path.endsWith('/session')) return route.fulfill({ json: { items: [{ kind: 'question', title: '排序章节', notes: '流程笔记', question: { id: 'order1', question_type: 'ordering', question_text: '请排序', options: [{ label: 'A', text: '第一步' }, { label: 'B', text: '第二步' }] }, source: {} }] } })
    if (path.endsWith('/answers')) { submitted = JSON.parse(route.request().postDataJSON().answer); return route.fulfill({ json: { is_correct: true, correct_answer: ['B', 'A'], analysis: '顺序解析' } }) }
    return route.fulfill({ json: [] })
  })
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('示例文档', { exact: true }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '智能复习' }).click()
  await expect(page.getByRole('button', { name: 'A 上移' })).toBeDisabled()
  await page.getByRole('button', { name: 'B 上移' }).click()
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('顺序解析')).toBeVisible()
  expect(submitted).toEqual(['B', 'A'])
})

test('learning workbench opens with document and budget controls', async ({ page }) => {
  await page.goto('/learning')
  await expect(page.getByRole('heading', { name: '学习工作台' })).toBeVisible()
  await expect(page.getByLabel('习题数量')).toHaveValue('10')
  await expect(page.getByLabel('卡片数量')).toHaveValue('10')
  await expect(page.getByRole('button', { name: '生成学习包', exact: true })).toBeDisabled()
})
