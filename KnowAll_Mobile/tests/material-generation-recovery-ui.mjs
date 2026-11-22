// Real Expo + production HTTP generation/SQLite; no paid AI and no native-device claim.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const theme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390, height = Number(process.env.MOBILE_UI_HEIGHT) || 844
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme })
const page = await context.newPage(), errors = [], posts = []
const fixture = 'http://127.0.0.1:8898', headers = { Authorization: 'Bearer local-ui-test-token' }
const key = 'material-generation:ui-test:bio'
let packFault = '', refuse = false
page.on('pageerror', error => errors.push(error.message))
await page.addInitScript(() => {
  const read = Storage.prototype.getItem, write = Storage.prototype.setItem, remove = Storage.prototype.removeItem
  window.__recoveryStored = key => read.call(localStorage, key)
  Storage.prototype.getItem = function (key) { if (read.call(this, 'recovery-read-fault') === key) throw Error('Read unavailable'); return read.call(this, key) }
  Storage.prototype.setItem = function (key, value) { if (read.call(this, 'recovery-write-fault') === key) throw Error('Storage full'); return write.call(this, key, value) }
  Storage.prototype.removeItem = function (key) { if (read.call(this, 'recovery-remove-fault') === key) throw Error('Remove unavailable'); return remove.call(this, key) }
  if (!read.call(localStorage, 'knowall_token')) {
    localStorage.setItem('knowall_token', 'local-ui-test-token')
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
  }
})
await page.route('**/api/**', async route => {
  const url = new URL(route.request().url()), path = url.pathname
  if (path.endsWith('/learning/generate')) {
    posts.push(route.request().postDataJSON())
    if (refuse) return route.fulfill({ status: 409, json: { detail: 'Source changed' } })
  }
  try {
    const response = await route.fetch({ url: `${fixture}${path}${url.search}` })
    if (path.endsWith('/learning/documents/bio/pack') && packFault && response.ok()) {
      if (packFault === 'network') return route.fulfill({ status: 503, json: { detail: 'Offline' } })
      const value = await response.json()
      return route.fulfill({ json: packFault === 'source' ? { ...value, source_hash: 'another-source' } : { ...value, config: { ...value.config, model: 'another-model' } } })
    }
    return await route.fulfill({ response })
  } catch (error) { if (!/disposed|closed|Target page/.test(String(error))) throw error }
})
const raw = () => page.evaluate(key => window.__recoveryStored(key), key)
const record = async () => JSON.parse(await raw())
const records = async () => (await context.request.get(`${fixture}/fixture/packs`, { headers })).json()
const gate = async action => expect((await context.request.post(`${fixture}/fixture/generation-gate/${action}`, { headers })).ok()).toBeTruthy()
const openTools = () => page.getByRole('button', { name: '学习工具', exact: true }).click()
const closeTools = () => page.getByRole('button', { name: '关闭学习工具', exact: true }).click()
const form = () => page.getByRole('button', { name: '整理学习内容', exact: true }).click()
const generate = () => page.getByRole('button', { name: '生成学习包', exact: true })
const check = () => page.getByRole('button', { name: '检查已生成内容', exact: true }).click()
const reloadTools = async id => { await page.goto(`http://localhost:8096/material?id=${id}`); await expect(page.getByRole('button', { name: '学习工具', exact: true })).toBeEnabled({ timeout: 120000 }); await openTools() }
const screenshot = async name => {
  // Paper's JS-driven modal animation is not disabled by Playwright's CSS animation option.
  await page.waitForTimeout(350)
  return page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${process.env.MOBILE_UI_EVIDENCE_ROUND || 'latest-recovery'}-${name}-${theme}-${width}.png`, import.meta.url)), fullPage: true })
}
try {
  expect((await context.request.post(`${fixture}/fixture/reset`, { headers })).ok()).toBeTruthy()
  await reloadTools('bio'); await form()
  await page.getByRole('textbox', { name: '生成习题数量', exact: true }).fill('2')
  await page.getByRole('textbox', { name: '生成知识卡数量', exact: true }).fill('1')
  await page.getByRole('textbox', { name: '学习生成模型', exact: true }).fill('fixture-recovery')
  await expect(generate()).toBeEnabled(); await gate('block'); await generate().click()
  await expect.poll(record).toMatchObject({ accountId: 'ui-test', documentId: 'bio', settings: { question_count: 2, card_count: 1, model: 'fixture-recovery' } })
  await expect.poll(() => posts.length).toBe(1)
  const submitted = await record()
  await closeTools(); await page.getByRole('button', { name: '返回我的资料', exact: true }).click()
  await reloadTools('history')
  await expect(page.getByText('上次生成结果待确认', { exact: true })).toBeHidden()
  await expect(page.getByText('这份资料还没有整理学习内容。')).toBeVisible()
  await reloadTools('bio')
  await expect(page.getByText('上次生成结果待确认', { exact: true })).toBeVisible()
  await screenshot('generation-pending')
  await check(); await expect(page.getByText('暂时无法确认生成结果。请稍后再检查，原资料仍可阅读。')).toBeVisible()
  expect(await record()).toEqual(submitted); expect(posts).toHaveLength(1)
  await page.reload(); await openTools()
  await expect(page.getByText('上次生成结果待确认', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看上次生成配置', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '生成习题数量' })).toHaveValue('2')
  await expect(page.getByRole('textbox', { name: '学习生成模型' })).toHaveValue('fixture-recovery')
  await expect(generate()).toBeDisabled(); expect(posts).toHaveLength(1)
  await page.getByRole('button', { name: '保留配置，重新尝试', exact: true }).click()
  await expect(page.getByText('重新尝试可能重复生成', { exact: true })).toBeVisible()
  await screenshot('generation-retry-confirm')
  await page.getByRole('button', { name: '继续检查结果', exact: true }).click()
  expect(await record()).toEqual(submitted); expect(posts).toHaveLength(1)
  await gate('release'); await expect.poll(async () => (await records()).packs.length).toBe(1)
  for (const fault of ['config', 'source', 'network']) {
    packFault = fault; await check()
    await expect(page.getByText('暂时无法确认生成结果。请稍后再检查，原资料仍可阅读。')).toBeVisible()
    expect(await record()).toEqual(submitted); expect(posts).toHaveLength(1)
  }
  packFault = ''
  await page.evaluate(key => localStorage.setItem('recovery-remove-fault', key), key)
  await check(); await expect(page.getByText('暂时无法确认生成结果。请稍后再检查，原资料仍可阅读。')).toBeVisible()
  expect(await record()).toEqual(submitted)
  await page.evaluate(() => localStorage.removeItem('recovery-remove-fault'))
  await check(); await expect(page.getByText('学习内容已整理，可开始练习或查看知识卡。')).toBeVisible()
  expect(await raw()).toBeNull(); expect(posts).toHaveLength(1)
  await screenshot('generation-recovered')

  // Saving a local journal is a precondition of the actual POST.
  await form(); await page.getByRole('textbox', { name: '学习生成模型' }).fill('fixture-storage')
  await page.evaluate(key => localStorage.setItem('recovery-write-fault', key), key)
  await expect(generate()).toBeEnabled(); await generate().click()
  await expect(page.getByText('提交记录未能保存，尚未发送生成请求。请检查设备存储后重试。')).toBeVisible()
  expect(await raw()).toBeNull(); expect(posts).toHaveLength(1)
  await page.evaluate(() => localStorage.removeItem('recovery-write-fault'))
  await page.evaluate(key => localStorage.setItem('recovery-read-fault', key), key)
  await page.reload(); await openTools(); await form()
  await expect(page.getByText('生成记录读取失败，暂不能发起新生成。原资料仍可阅读。')).toBeVisible()
  await expect(page.getByRole('button', { name: '处理损坏的生成记录', exact: true })).toBeHidden()
  await expect(generate()).toBeDisabled(); expect(posts).toHaveLength(1)
  await page.evaluate(() => localStorage.removeItem('recovery-read-fault'))
  await page.getByRole('button', { name: '重试读取生成记录', exact: true }).click()
  await expect(generate()).toBeEnabled()
  await page.evaluate(key => localStorage.setItem(key, '{'), key)
  await page.reload(); await openTools(); await form()
  await expect(page.getByText('生成记录读取失败，暂不能发起新生成。原资料仍可阅读。')).toBeVisible()
  await expect(generate()).toBeDisabled(); expect(await raw()).toBe('{'); expect(posts).toHaveLength(1)
  await screenshot('generation-storage-protection')
  const handleCorrupt = () => page.getByRole('button', { name: '处理损坏的生成记录', exact: true }).click()
  const confirmCorrupt = () => page.getByRole('button', { name: '确认清除损坏记录', exact: true }).click()
  await handleCorrupt()
  await expect(page.getByText('清除损坏的本机记录？', { exact: true })).toBeVisible()
  await screenshot('generation-corrupt-confirm')
  await page.getByRole('button', { name: '保留损坏记录', exact: true }).click()
  expect(await raw()).toBe('{'); expect(posts).toHaveLength(1)
  await handleCorrupt()
  await page.evaluate(key => localStorage.setItem('recovery-remove-fault', key), key)
  await confirmCorrupt()
  await expect(page.getByText('损坏记录清理失败，已保留原记录。请稍后重试。').first()).toBeVisible()
  expect(await raw()).toBe('{'); expect(posts).toHaveLength(1)
  await page.evaluate(() => localStorage.removeItem('recovery-remove-fault'))
  // A valid journal written while confirmation is open must be recovered, not deleted.
  await page.evaluate(({ key, submitted }) => localStorage.setItem(key, JSON.stringify(submitted)), { key, submitted })
  await confirmCorrupt()
  await expect(page.getByText('生成记录已变化，未清除。请核对当前记录后再操作。').first()).toBeVisible()
  expect(await record()).toEqual(submitted); expect(posts).toHaveLength(1)
  await expect(page.getByText('清除损坏的本机记录？', { exact: true })).toBeHidden()
  await check(); await expect.poll(raw).toBeNull(); expect(posts).toHaveLength(1)
  await expect(page.getByText('学习内容已整理，可开始练习或查看知识卡。')).toBeVisible()
  await form()
  await page.evaluate(key => localStorage.setItem(key, '{'), key)
  await page.reload(); await openTools(); await form(); await handleCorrupt()
  await page.evaluate(key => localStorage.setItem(key, '{changed'), key)
  await confirmCorrupt()
  await expect(page.getByText('生成记录已变化，未清除。请核对当前记录后再操作。').first()).toBeVisible()
  expect(await raw()).toBe('{changed'); expect(posts).toHaveLength(1)
  await handleCorrupt(); await confirmCorrupt()
  await expect(page.getByText('损坏的本机记录已清除。请先检查已有学习内容；需要重新生成时再点击生成。').first()).toBeVisible()
  expect(await raw()).toBeNull(); expect(posts).toHaveLength(1)
  await expect(generate()).toBeEnabled()
  await screenshot('generation-corrupt-recovered')

  // Unknown result + explicit confirmed retry clears only locally, with no automatic POST.
  await page.evaluate(({ key, submitted }) => localStorage.setItem(key, JSON.stringify(submitted)), { key, submitted })
  await page.reload(); await openTools()
  await page.getByRole('button', { name: '查看上次生成配置', exact: true }).click()
  await page.getByRole('button', { name: '保留配置，重新尝试', exact: true }).click()
  await page.evaluate(key => localStorage.setItem('recovery-remove-fault', key), key)
  await page.getByRole('button', { name: '确认保留配置重试', exact: true }).click()
  await expect(page.getByText('本机记录清理失败，已保留原记录。请稍后重试。').first()).toBeVisible()
  expect(await record()).toEqual(submitted); expect(posts).toHaveLength(1)
  await page.evaluate(() => localStorage.removeItem('recovery-remove-fault'))
  await page.getByRole('button', { name: '确认保留配置重试', exact: true }).click()
  await expect(generate()).toBeEnabled(); expect(await raw()).toBeNull(); expect(posts).toHaveLength(1)
  await expect(page.getByRole('textbox', { name: '学习生成模型' })).toHaveValue('fixture-recovery')
  refuse = true; await generate().click()
  await expect(page.getByText('资料已变化，请重新预览后再整理。')).toBeVisible()
  expect(await raw()).toBeNull(); expect(posts).toHaveLength(2)
  expect((await records()).real_ai_calls).toBe(0)
  expect(errors).toEqual([]); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  console.log(`PASS (${theme}/${width}): real HTTP blocked generation, leave/other-document/reopen/reload, restored config, GET-only matching/source/network/deletion protection, explicit retry cancel/confirm, corruption/read/write faults without POST, confirmed corrupt cleanup/cancel/delete failure/valid and corrupt replacement protection, rejection cleanup; zero real AI.`)
} finally { await gate('release').catch(() => {}); await browser.close() }
