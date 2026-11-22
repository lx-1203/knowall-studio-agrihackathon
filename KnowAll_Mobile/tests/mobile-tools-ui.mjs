import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mockTools } from './mobile-tools-fixture.mjs'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const theme = process.env.MOBILE_UI_THEME || 'light', width = Number(process.env.MOBILE_UI_WIDTH || 390), base = process.env.MOBILE_UI_URL || 'http://localhost:8096'
const page = await browser.newPage({ viewport: { width, height: 844 }, colorScheme: theme })
const state = await mockTools(page), testCase = process.env.MOBILE_UI_CASE || 'flashcards', errors = []
page.on('pageerror', e => errors.push(e.message))
const text = value => page.getByText(value, { exact: true })
const button = value => page.getByRole('button', { name: value, exact: true })
const calls = (path, method) => state.calls.filter(c => c.path === path && c.method === method)
const shot = async name => { await page.mouse.move(0, 0); await page.waitForTimeout(400); await page.screenshot({ path: fileURLToPath(new URL('../docs/patrol/' + (process.env.MOBILE_UI_EVIDENCE_DIR || 'ui03-01a12efd') + '/after-' + name + '-' + theme + '-' + width + '.png', import.meta.url)) }) }
const checkWidth = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
try {
  if (testCase === 'draft') {
    await page.addInitScript(() => localStorage.setItem('quiz-session:tools-demo', '{broken'))
    await page.goto(base + '/quiz')
    await expect(page.getByText(/练习草稿没有读好/)).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('quiz-session:tools-demo'))).toBe('{broken')
    await button('清除损坏草稿').click(); await button('取消').click()
    expect(await page.evaluate(() => localStorage.getItem('quiz-session:tools-demo'))).toBe('{broken')
    await button('清除损坏草稿').click(); await button('确认清除').click()
    await expect(button('开始练习')).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('quiz-session:tools-demo'))).toBeNull()
    await page.evaluate(() => {
      const original = Storage.prototype.setItem
      window.restoreToolStorage = () => { Storage.prototype.setItem = original }
      Storage.prototype.setItem = function(key, value) {
        if (key === 'quiz-session:tools-demo') throw new Error('Isolated quota failure')
        return original.call(this, key, value)
      }
    })
    await button('开始练习').click()
    await expect(text('本机进度没有保存成功，请重试后再离开。')).toBeVisible()
    await page.evaluate(() => window.restoreToolStorage())
    await button('重试').click(); await expect(text('本机进度已保存')).toBeVisible()
    expect(JSON.parse(await page.evaluate(() => localStorage.getItem('quiz-session:tools-demo'))).accountId).toBe('tools-demo')
    await button('交卷').click(); await expect(page.getByText(/已答 0 \/ 3 题/)).toBeVisible(); await button('取消').click()
  }
  if (testCase === 'flashcards') {
    await page.goto(base + '/flashcards'); await page.getByText(/遗传学/).first().click()
    await expect.poll(() => calls('/flashcards/due', 'GET').at(-1)?.params.deck_id).toBe('d2')
    await expect(text('DNA 的作用？')).toBeVisible(); await checkWidth(); await shot('flashcards')
    await button('查看答案').click(); await expect(text('保存遗传信息。')).toBeVisible()
    state.faults['/flashcards/review'] = true
    await button('记住了').click(); await expect(page.getByText(/评分未确认/)).toBeVisible()
    expect(calls('/flashcards/review', 'POST')).toHaveLength(1)
    await expect(text('保存遗传信息。')).toBeVisible()
    delete state.faults['/flashcards/review']
    state.delays['/gamification/record-activity'] = 5000
    await button('重试').click(); await button('查看答案').click(); await button('记住了').click()
    await expect(text('这一组复习完成了')).toBeVisible()
    expect(calls('/flashcards/review', 'POST')).toHaveLength(2)
    await expect(page.getByText(/细胞生物学/).first()).toBeEnabled({ timeout: 1000 })
    await shot('flashcards-completed')
    await page.getByText(/细胞生物学/).first().click({ timeout: 1000 }); await expect(text('细胞膜的作用？')).toBeVisible({ timeout: 1000 })
    await button('返回复习').click(); await expect(page).toHaveURL(/review/)
  }
  if (testCase === 'quiz') {
    await page.goto(base + '/quiz'); await expect(button('开始练习')).toBeEnabled()
    await shot('quiz'); await checkWidth(); await button('开始练习').click()
    await expect(text('细胞核的主要作用是什么？')).toBeVisible()
    await page.getByRole('radio', { name: '选择 A：储存遗传信息' }).click(); await button('下一题').click()
    await page.getByRole('radio', { name: '选择 true：正确' }).click()
    await page.reload(); await button('继续上次练习').click()
    await expect(page.getByRole('radio', { name: '选择 true：正确' })).toHaveAttribute('aria-checked', 'true')
    await button('下一题').click()
    await page.getByRole('checkbox', { name: '选择 A：DNA' }).click(); await page.getByRole('checkbox', { name: '选择 B：RNA' }).click()
    await shot('quiz-answer'); await button('交卷').click(); await button('取消').click()
    expect(calls('/quiz/exam/submit', 'POST')).toHaveLength(0)
    state.faults['/quiz/exam/submit'] = true
    await button('交卷').click(); await button('确认交卷').click(); await expect(text('隔离测试故障')).toBeVisible()
    expect(calls('/quiz/exam/submit', 'POST')).toHaveLength(1)
    delete state.faults['/quiz/exam/submit']
    await button('交卷').click(); await button('确认交卷').click(); await expect(text('练习结果')).toBeVisible()
    await expect(text('A. 储存遗传信息')).toBeVisible()
    expect(calls('/quiz/exam/submit', 'POST')).toHaveLength(2)
    expect(JSON.parse(calls('/quiz/exam/submit', 'POST').at(-1).body).answers).toEqual({ q1: 'A', q2: 'true', q3: 'A,B' })
    await shot('quiz-results'); await page.reload(); await button('继续上次练习').click()
    await expect(text('练习结果')).toBeVisible()
    await button('回到题库').click(); await button('确认清除').click(); await expect(button('开始练习')).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('quiz-session:tools-demo'))).toBeNull()
  }
  if (testCase === 'recitation') {
    await page.goto(base + '/recitation'); await expect(text('细胞结构')).toBeVisible(); await checkWidth(); await shot('recitation')
    await button('遮字背诵').click(); await expect(button('揭示第1处')).toBeVisible()
    await expect(text('细胞核储存遗传信息。')).toHaveCount(0)
    await button('揭示第1处').click(); await expect(text('细胞核')).toBeVisible()
    await button('查看原文').click(); await expect(text('细胞核储存遗传信息。').last()).toBeVisible(); await shot('recitation-hide')
    await button('返回背诵列表').click(); await button('开始默写').click()
    await page.getByLabel('默写内容', { exact: true }).fill('细胞核保存遗传信息。')
    await button('返回背诵列表').click(); await button('开始默写').click()
    await expect(page.getByLabel('默写内容', { exact: true })).toHaveValue('细胞核保存遗传信息。')
    state.faults['/recitation/dictation/submit'] = true
    await button('检查默写').click(); await expect(text('隔离测试故障')).toBeVisible()
    expect(calls('/recitation/dictation/submit', 'POST')).toHaveLength(1)
    delete state.faults['/recitation/dictation/submit']
    await button('检查默写').click(); await expect(text('漏写：储存')).toBeVisible(); await expect(text('多写：保存')).toBeVisible()
    await shot('recitation-result'); await button('记住了').click()
    await expect(text('已安排下次复习')).toBeVisible(); expect(calls('/recitation/review', 'POST')).toHaveLength(1)
    await button('返回背诵列表').click(); await button('新建').click(); await button('保存内容').click()
    await expect(text('请填写标题和背诵内容。')).toBeVisible()
    await page.getByLabel('背诵标题', { exact: true }).fill('第二段')
    await page.getByLabel('背诵原文', { exact: true }).fill('线粒体提供能量。')
    await button('保存内容').click(); await expect(text('第二段')).toBeVisible()
    await button('删除').last().click(); await button('取消').click(); expect(calls('/recitation/passages/p-new', 'DELETE')).toHaveLength(0)
    await button('删除').last().click(); await button('确认删除').click(); await expect(text('第二段')).toHaveCount(0)
  }
  if (testCase === 'chat') {
    await page.goto(base + '/chat'); await expect(button('发送问题')).toBeDisabled(); await checkWidth(); await shot('chat')
    await button('用例子解释一个概念').click(); expect(calls('/chat/assistant', 'POST')).toHaveLength(0)
    await page.getByLabel('你的问题', { exact: true }).fill('细胞核有什么作用？')
    state.faults['/chat/assistant'] = true
    await button('发送问题').click(); await expect(text('隔离测试故障')).toBeVisible()
    expect(calls('/chat/assistant', 'POST')).toHaveLength(1)
    await expect(page.getByLabel('你的问题', { exact: true })).toHaveValue('细胞核有什么作用？')
    delete state.faults['/chat/assistant']; state.delays['/chat/assistant'] = 300; state.delays['/chat/conversations'] = 5000
    await button('发送问题').dblclick({ delay: 20 }); await expect.poll(() => state.messages.length).toBe(1)
    await expect(page.getByLabel('你的问题', { exact: true })).toBeEditable({ timeout: 1000 })
    await expect(text('这是隔离测试回复：细胞核保存遗传信息。')).toBeVisible()
    delete state.delays['/chat/conversations']
    expect(calls('/chat/assistant', 'POST')).toHaveLength(2); await shot('chat-answer')
    await button('历史对话').click(); await text('细胞结构讨论').click()
    await expect(text('细胞核保存遗传信息。')).toBeVisible()
    state.delays['/chat/conversations/conv1'] = 500
    await button('历史对话').click(); await text('细胞结构讨论').last().click()
    await button('历史对话').click(); await text('遗传学讨论').click()
    await expect(text('DNA 是遗传物质。')).toBeVisible(); await page.waitForTimeout(700)
    await expect(text('DNA 是遗传物质。')).toBeVisible()
    await button('新对话').click(); await expect(text('今天想弄懂什么？')).toBeVisible()
    await expect(page.getByLabel('你的问题', { exact: true })).toHaveValue('')
  }
  expect(errors).toEqual([])
  expect(state.calls.some(c => /generate|recommend/.test(c.path) && c.method !== 'GET')).toBe(false)
  console.log('PASS ' + testCase + '/' + theme + '/' + width + ' requests=' + state.calls.length)
} finally { await browser.close() }
