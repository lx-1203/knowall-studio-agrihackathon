// Real Expo web + production generation/preview routes over isolated SQLite, deterministic generator.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const colorScheme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390
const height = Number(process.env.MOBILE_UI_HEIGHT) || 844
const context = await browser.newContext({ viewport: { width, height }, colorScheme })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
const fixture = 'http://127.0.0.1:8898', headers = { Authorization: 'Bearer local-ui-test-token' }
let previewFault = true, lostResponse = false, unprocessedFault = false, wrongRecovery = false, delayedPreview = null, releasePreview = null
const requests = []
const records = async () => (await context.request.get(`${fixture}/fixture/packs`, { headers })).json()
await page.addInitScript(() => {
  localStorage.setItem('knowall_token', 'local-ui-test-token')
  localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
})
await page.route('**/api/**', async route => {
  const url = new URL(route.request().url()), path = url.pathname, body = route.request().postDataJSON()
  requests.push({ path, body, auth: route.request().headers()['authorization'] })
  if (path.includes('/learning/') && path.endsWith('/preview')) {
    if (previewFault) return route.fulfill({ status: 500, json: { detail: 'Unprocessed fixture preview error' } })
    if (url.searchParams.get('question_count') === '7' && delayedPreview) await delayedPreview
  }
  if (path.endsWith('/learning/generate') && unprocessedFault) return route.fulfill({ status: 500, json: { detail: 'Unprocessed fixture generation error' } })
  try {
    const response = await route.fetch({ url: `${fixture}${path}${url.search}` })
    if (path.endsWith('/learning/documents/bio/pack') && wrongRecovery) {
      wrongRecovery = false
      const result = await response.json()
      return route.fulfill({ status: 200, json: { ...result, config: { ...result.config, model: 'another-submission' } } })
    }
    if (path.endsWith('/learning/generate') && lostResponse) { lostResponse = false; return route.fulfill({ status: 503, json: { detail: 'Lost response after actual SQLite commit' } }) }
    return await route.fulfill({ response })
  } catch (error) { if (!/disposed|closed/.test(String(error))) throw error }
})
const openDocument = title => page.getByRole('button', { name: '打开学习', exact: true })
  .filter({ has: page.getByText(title, { exact: true }) }).click()
const openTools = () => page.getByRole('button', { name: '学习工具', exact: true }).click()
const form = () => page.getByRole('button', { name: '整理学习内容', exact: true }).click()
const generateButton = () => page.getByRole('button', { name: '生成学习包', exact: true })
const setModel = value => page.getByRole('textbox', { name: '学习生成模型', exact: true }).fill(value)
const submit = async () => { await expect(generateButton()).toBeEnabled(); await generateButton().click() }
const screenshot = async name => {
  await expect.poll(() => page.getByTestId('modal-surface').evaluate(e => Number(getComputedStyle(e).opacity) >= 0.99)).toBeTruthy()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${process.env.MOBILE_UI_EVIDENCE_ROUND || 'round06'}-${name}-${colorScheme}-${width}.png`, import.meta.url)), fullPage: true })
}
try {
  expect((await context.request.post(`${fixture}/fixture/reset`, { headers })).ok()).toBeTruthy()
  await page.goto('http://localhost:8096')
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible({ timeout: 120000 })
  await openDocument('生物教材.md'); await openTools()
  await expect(page.getByText('这份资料还没有整理学习内容。')).toBeVisible()
  expect((await records()).packs).toHaveLength(0)
  await form()
  await expect(page.getByText('章节分配加载失败，请重试。')).toBeVisible({ timeout: 12000 })
  await expect(generateButton()).toBeDisabled()
  expect(requests.filter(r => r.path.endsWith('/learning/generate'))).toHaveLength(0)
  previewFault = false
  await page.getByRole('textbox', { name: '生成习题数量', exact: true }).fill('4')
  await page.getByRole('textbox', { name: '生成知识卡数量', exact: true }).fill('2')
  await setModel('fixture-standard')
  await page.getByRole('checkbox', { name: '生成题型：匹配', exact: true }).click()
  await page.getByRole('checkbox', { name: '生成题型：填空', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: '生成题型：填空', exact: true })).toBeChecked()
  await expect(page.getByText('2 个章节 · 查看分配')).toBeVisible()
  await expect(page.getByText('习题 2 · 知识卡 1', { exact: true })).toHaveCount(2)
  await page.getByText('整理这份资料的学习内容', { exact: true }).scrollIntoViewIfNeeded()
  await screenshot('configuration')
  await page.getByText('2 个章节 · 查看分配').scrollIntoViewIfNeeded()
  await screenshot('budget')
  await page.getByRole('textbox', { name: '生成习题数量', exact: true }).fill('1.5')
  await expect(generateButton()).toBeDisabled()
  await expect(page.getByText('习题和知识卡数量需为0至100的整数。')).toBeVisible()
  delayedPreview = new Promise(resolve => { releasePreview = resolve })
  await page.getByRole('textbox', { name: '生成习题数量', exact: true }).fill('7')
  await page.waitForTimeout(500)
  await expect(generateButton()).toBeDisabled()
  await page.getByRole('textbox', { name: '生成习题数量', exact: true }).fill('6')
  await expect(page.getByText('习题 3 · 知识卡 1', { exact: true })).toHaveCount(2)
  releasePreview(); delayedPreview = null
  await expect(generateButton()).toBeEnabled()
  await generateButton().dblclick()
  await expect(page.getByText('学习内容已整理，可开始练习或查看知识卡。')).toBeVisible()
  expect(requests.filter(r => r.path.endsWith('/learning/generate'))).toHaveLength(1)
  const first = (await records()).packs[0]
  expect(first.document_id).toBe('bio'); expect(first.question_count).toBe(6); expect(first.card_count).toBe(2)
  expect(first.config.question_types).toEqual(['single_choice', 'fill_blank', 'matching'])
  await form(); await expect(page.getByRole('textbox', { name: '生成习题数量' })).toHaveValue('6')
  await submit(); await expect(page.getByText('已复用这份资料的学习内容。')).toBeVisible()
  expect((await records()).packs).toHaveLength(1)
  await form(); await setModel('fixture-partial'); await submit()
  await expect(page.getByText('2 个章节存在缺漏，已保留可用内容。')).toBeVisible()
  expect((await records()).packs).toHaveLength(2)
  await screenshot('partial')
  await form(); await setModel('fixture-all-fail'); await submit()
  await expect(page.getByText('各章节均未生成成功，请检查模型设置后重新整理。原资料仍可阅读。')).toBeVisible()
  await expect(generateButton()).toBeEnabled()
  expect((await records()).packs).toHaveLength(3)
  await page.getByRole('button', { name: '返回学习工具', exact: true }).click()
  await expect(page.getByText('已整理：第一章 细胞', { exact: true })).toBeVisible()
  await form(); await setModel('fixture-lost'); lostResponse = true; await submit()
  await expect(page.getByText('生成结果尚未确认。请先检查已生成内容，避免重复请求。')).toBeVisible()
  await page.waitForTimeout(3200)
  expect(requests.filter(r => r.path.endsWith('/learning/generate') && r.body.model === 'fixture-lost')).toHaveLength(1)
  expect((await records()).packs).toHaveLength(4)
  await expect(generateButton()).toBeDisabled()
  wrongRecovery = true
  await page.getByRole('button', { name: '检查已生成内容', exact: true }).click()
  await expect(page.getByText('暂时无法确认生成结果。请稍后再检查，原资料仍可阅读。')).toBeVisible()
  await expect(generateButton()).toBeDisabled()
  await page.getByRole('button', { name: '检查已生成内容', exact: true }).click()
  await expect(page.getByText('学习内容已整理，可开始练习或查看知识卡。')).toBeVisible()
  await form(); await setModel('fixture-unprocessed'); unprocessedFault = true; await submit()
  await expect(page.getByRole('button', { name: '检查已生成内容', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '检查已生成内容', exact: true }).click()
  await expect(page.getByText('暂时无法确认生成结果。请稍后再检查，原资料仍可阅读。')).toBeVisible()
  await expect(generateButton()).toBeDisabled()
  expect((await records()).packs).toHaveLength(4)
  unprocessedFault = false
  await page.getByRole('button', { name: '保留配置，重新尝试', exact: true }).click()
  await page.getByRole('button', { name: '确认保留配置重试', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '学习生成模型' })).toHaveValue('fixture-unprocessed')
  await submit(); await expect(page.getByText('学习内容已整理，可开始练习或查看知识卡。')).toBeVisible()
  expect((await records()).packs).toHaveLength(5)
  await form(); await setModel('fixture-slow'); await submit()
  await expect(page.getByRole('textbox', { name: '学习生成模型' })).not.toBeEditable()
  await page.getByRole('button', { name: '关闭学习工具', exact: true }).click()
  await page.getByRole('button', { name: '返回我的资料', exact: true }).click()
  await openDocument('历史试卷.md'); await openTools()
  await expect(page.getByText('这份资料还没有整理学习内容。')).toBeVisible()
  await expect.poll(async () => (await records()).packs.length).toBe(6)
  await expect(page.getByText('学习内容已整理，可开始练习或查看知识卡。')).toBeHidden()
  await form(); await expect(page.getByRole('textbox', { name: '生成习题数量' })).toHaveValue('10')
  await page.getByRole('button', { name: '关闭学习工具', exact: true }).click()
  await page.getByRole('button', { name: '返回我的资料', exact: true }).click()
  await openDocument('待解析资料.pdf'); await openTools()
  await expect(page.getByRole('button', { name: '整理学习内容', exact: true })).toBeDisabled()
  const denied = await context.request.post(`${fixture}/api/v1/learning/generate`, { headers: { Authorization: 'Bearer other-account-token' }, data: { document_id: 'bio', question_count: 1, card_count: 1, model: 'fixture-standard' } })
  expect([403, 404]).toContain(denied.status())
  expect((await records()).real_ai_calls).toBe(0)
  expect(requests.filter(r => r.path.includes('/learning/')).every(r => r.auth === headers.Authorization)).toBeTruthy()
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  console.log(`PASS (${colorScheme}, ${width}px): production HTTP preview/config/double-click/cache/partial/all-failed persistence, response-loss recovery without replay, explicit retry, cancelled old budgets, document isolation, empty-source and ownership; zero real AI calls, no overflow/page errors.`)
} finally { releasePreview?.(); await browser.close() }
