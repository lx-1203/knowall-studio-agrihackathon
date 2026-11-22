// Exercises the actual Expo app through react-native-web. This supplements,
// but does not replace, native device verification. Run Expo on port 8096 first.
import { createRequire } from 'node:module'
import fs from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const colorScheme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const evidenceRound = process.env.MOBILE_UI_ROUND || 'latest'
const context = await browser.newContext({ viewport: { width: Number(process.env.MOBILE_UI_WIDTH) || 390, height: Number(process.env.MOBILE_UI_HEIGHT) || 844 }, colorScheme })
const page = await context.newPage()
const errors = []
const iconWarnings = []
page.on('pageerror', error => errors.push(error.stack || error.message))
page.on('console', message => { if (message.text().includes('none of the required icon libraries')) iconWarnings.push(message.text()) })
const requests = []
let packState = 'ready'
let failAnswer = false
const doc = { id: 'bio', filename: '生物教材.md', subject: '生物', material_kind: 'knowledge', status: 'ready', file_type: 'md', page_count: 2 }
await page.addInitScript(() => {
  localStorage.setItem('knowall_token', 'local-ui-test-token')
  localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  requests.push({ path, method: route.request().method(), body: route.request().postDataJSON() })
  let result = []
  if (path.endsWith('/documents/')) result = [doc]
  else if (path.endsWith('/documents/bio')) result = doc
  else if (path.endsWith('/documents/bio/chunks')) result = [
    { id: 'c1', index: 0, text: '第一章 细胞\n细胞是生命活动的基本单位。', page_range: '1' },
    { id: 'c2', index: 1, text: '第二章 遗传\nDNA 保存遗传信息。', page_range: '2' },
  ]
  else if (path.endsWith('/learning/documents/bio/pack')) {
    if (packState === 'error') return route.fulfill({ status: 400, json: { detail: 'Mock load failure' } })
    result = packState === 'empty' ? null : { id: 'pack-bio', stale: packState === 'stale', sections: [{ id: 's2', title: '遗传', notes: 'DNA 是遗传信息的载体。', chunk_ids: ['c2'], flashcards: [{ id: 'card1', front: '遗传信息的载体是什么？', back: 'DNA' }] }] }
  }
  else if (path.endsWith('/learning/packs/pack-bio/session')) result = { items: [{ kind: 'question', title: '遗传', source: { chunk_ids: ['c2'] }, question: { id: 'q1', question_text: '遗传信息的载体是什么？', question_type: 'single_choice', options: [{ label: 'A', text: 'DNA' }, { label: 'B', text: '脂质' }] } }] }
  else if (path.endsWith('/learning/packs/pack-bio/answers')) {
    if (failAnswer) return route.fulfill({ status: 400, json: { detail: 'Mock submit failure' } })
    result = { is_correct: true, correct_answer: 'A', analysis: 'DNA 保存遗传信息。', source: { chunk_ids: ['c2'] } }
  }
  else if (path.endsWith('/chat/assistant')) result = { message: '这段资料讲的是 DNA 保存遗传信息。' }
  await route.fulfill({ json: result })
})
try {
  await page.goto('http://localhost:8096', { waitUntil: 'domcontentloaded' })
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible({ timeout: 120000 })
  await expect(page.getByText('生物教材.md', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '打开学习' }).click()
  await expect(page.getByTestId('material-reading-content').getByText('第一章 细胞', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await expect(page.getByText('DNA 是遗传信息的载体。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '阅读对应原文' }).click()
  await expect(page.getByTestId('material-reading-content').getByText('第二章 遗传', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await page.getByText('导图', { exact: true }).click()
  await page.getByRole('button', { name: '全部收起' }).click()
  await expect(page.getByText('遗传信息的载体是什么？', { exact: true })).toBeHidden()
  await page.getByLabel('搜索导图', { exact: true }).fill('dna')
  await expect(page.getByText('遗传信息的载体是什么？', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看卡片答案' }).click()
  await expect(page.getByText('DNA', { exact: true })).toBeVisible()
  await expect.poll(() => page.getByTestId('modal-surface').evaluate(element => {
    for (let node = element; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 1) return false
    return true
  })).toBeTruthy()
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${evidenceRound}-expo-map-${colorScheme}.png`, import.meta.url)), fullPage: true })
  await page.getByLabel('搜索导图', { exact: true }).fill('找不到的知识点')
  await expect(page.getByText('没有匹配的分支')).toBeVisible()
  await page.getByRole('button', { name: '清空搜索' }).click()
  await page.getByRole('button', { name: '全部展开' }).click()
  await page.getByRole('button', { name: '阅读分支原文' }).click()
  await expect(page.getByTestId('material-reading-content').getByText('第二章 遗传', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await page.getByText('知识卡', { exact: true }).click()
  await page.getByRole('button', { name: '显示答案' }).click()
  await expect(page.getByText('DNA', { exact: true })).toBeVisible()
  await page.getByText('练习', { exact: true }).click()
  await page.getByRole('button', { name: '开始当前资料练习' }).click()
  await page.getByRole('radio', { name: '选择 A：DNA' }).click()
  failAnswer = true
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('提交失败，答案已保留，请重试。')).toBeVisible()
  await expect(page.getByRole('radio', { name: '选择 A：DNA' })).toBeChecked()
  failAnswer = false
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('回答正确', { exact: true })).toBeVisible()
  await page.getByText('问答', { exact: true }).click()
  await page.getByRole('textbox').fill('解释这段资料')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('这段资料讲的是 DNA 保存遗传信息。', { exact: true })).toBeVisible()
  const chat = requests.find(request => request.path.endsWith('/chat/assistant'))
  expect(chat.body.knowledge_context).toContain('DNA 保存遗传信息')
  expect(requests.some(request => request.path.includes('/quiz/questions'))).toBeFalsy()
  expect(errors).toEqual([])
  expect(iconWarnings).toEqual([])
  await page.getByRole('button', { name: '关闭学习工具' }).click()
  await expect(page.getByText('当前资料学习工具')).toBeHidden()
  await page.getByRole('button', { name: '当前资料笔记' }).click()
  await page.getByRole('textbox', { name: '我的学习笔记' }).fill('DNA 学习笔记，仅属于这份教材。')
  await page.getByRole('button', { name: '保存笔记' }).click()
  await expect(page.getByText('已保存到本机')).toBeVisible()
  await page.getByRole('button', { name: '返回阅读' }).click()
  await page.reload()
  await expect(page.getByTestId('material-reading-content').getByText('第二章 遗传', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '当前资料笔记' }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('DNA 学习笔记，仅属于这份教材。')
  await page.getByRole('button', { name: '返回阅读' }).click()
  await expect(page.getByText('这份资料的笔记')).toBeHidden()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await fs.mkdir(new URL('../docs/patrol/', import.meta.url), { recursive: true })
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${evidenceRound}-expo-reader-${colorScheme}.png`, import.meta.url)), fullPage: true })
  for (const state of ['stale', 'empty', 'error']) {
    packState = state
    await page.reload()
    await page.getByRole('button', { name: '学习工具', exact: true }).click()
    if (state === 'stale') {
      await page.getByText('练习', { exact: true }).click()
      await expect(page.getByRole('button', { name: '开始当前资料练习' })).toBeDisabled()
    } else if (state === 'empty') await expect(page.getByText('这份资料还没有整理学习内容。')).toBeVisible()
    else await expect(page.getByText('学习内容加载失败，原资料仍可阅读。')).toBeVisible()
    await page.getByText('导图', { exact: true }).click()
    if (state === 'stale') await expect(page.getByText('资料已更新，章节内容可能过期。')).toBeVisible()
    else {
      await expect(page.getByText(/原文结构 · 无需生成/)).toBeVisible()
      await expect(page.getByRole('button', { name: '阅读分支原文' })).toHaveCount(2)
    }
    await page.getByRole('button', { name: '关闭学习工具' }).click()
    await expect(page.getByText('当前资料学习工具')).toBeHidden()
    await expect(page.getByTestId('material-reading-content').getByText('第二章 遗传', { exact: false })).toBeVisible()
  }
  expect(errors).toEqual([])
  expect(iconWarnings).toEqual([])
  console.log(`PASS (${colorScheme}): Expo library, reading, mindmap search/folding/cards/source/raw fallback, practice, source-bound questions, answer retry, notes/progress restoration, stale/empty/error packs; no overflow, page errors or missing-icon warnings.`)
} finally { await browser.close() }
