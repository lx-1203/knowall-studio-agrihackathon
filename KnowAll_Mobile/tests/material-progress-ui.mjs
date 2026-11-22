// Actual Expo page and AsyncStorage web adapter. Browser checks are not native-device verification.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const colorScheme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390
const height = Number(process.env.MOBILE_UI_HEIGHT) || 844
const evidenceRound = process.env.MOBILE_UI_EVIDENCE_ROUND || 'latest-progress'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width, height }, colorScheme })
const page = await context.newPage()
const errors = [], posts = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()) })
await page.addInitScript(() => {
  const read = Storage.prototype.getItem, write = Storage.prototype.setItem
  window.__stored = key => read.call(localStorage, key)
  Storage.prototype.getItem = function (key) {
    if (read.call(this, 'progress-read-fault') === key) throw Error('Injected read failure')
    return read.call(this, key)
  }
  Storage.prototype.setItem = function (key, value) {
    if (read.call(this, 'progress-write-fault') === key) throw Error('Injected write failure')
    return write.call(this, key, value)
  }
  if (!read.call(localStorage, 'knowall_token')) {
    localStorage.setItem('knowall_token', 'test-token')
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
    localStorage.setItem('material-progress:ui-test:bio', JSON.stringify({ index: 2, fontSize: 24 }))
    localStorage.setItem('progress-read-fault', 'material-progress:ui-test:bio')
  }
})
const documents = ['bio', 'history'].map(id => ({ id, filename: id === 'bio' ? '生物教材.md' : '历史教材.md', file_type: 'md', status: 'ready', subject: id === 'bio' ? '生物' : '历史' }))
await page.route('**/api/**', route => {
  const path = new URL(route.request().url()).pathname
  const id = path.includes('history') ? 'history' : 'bio'
  if (path.endsWith('/documents/')) return route.fulfill({ json: documents })
  if (path.endsWith('/chunks')) return route.fulfill({ json: Array.from({ length: 4 }, (_, index) => ({ id: id + '-' + index, index, text: id + ' 正文第' + (index + 1) + '段。继续阅读和字号可用。', page_range: String(index + 1) })) })
  if (path.includes('/documents/') && !path.includes('/learning/')) return route.fulfill({ json: documents.find(doc => doc.id === id) })
  return route.fulfill({ json: null })
})
const stored = key => page.evaluate(key => window.__stored(key), key)
const readPosition = async (account = 'ui-test', id = 'bio') => JSON.parse(await stored('material-progress:' + account + ':' + id))
const text = (id = 'bio', number = 1) => page.getByText(id + ' 正文第' + number + '段。继续阅读和字号可用。', { exact: true })
const screenshot = name => page.screenshot({ path: fileURLToPath(new URL('../docs/patrol/' + evidenceRound + '-' + name + '-' + colorScheme + '-' + width + '.png', import.meta.url)), fullPage: true })
try {
  await page.goto('http://localhost:8096/material?id=bio')
  await expect(text()).toBeVisible({ timeout: 120000 })
  await expect(page.getByText('阅读进度读取失败，旧记录未被覆盖。可继续阅读，恢复存储后重试。')).toBeVisible()
  expect(await readPosition()).toEqual({ index: 2, fontSize: 24 })
  await screenshot('read-failure')
  await page.evaluate(() => localStorage.removeItem('progress-read-fault'))
  await page.getByRole('button', { name: '重试读取进度', exact: true }).click()
  await expect(text('bio', 3)).toBeVisible()
  await expect(text('bio', 3)).toHaveCSS('font-size', '24px')
  expect(await readPosition()).toEqual({ index: 2, fontSize: 24 })

  // Font changes alone during a read failure must not erase the saved section.
  await page.evaluate(() => localStorage.setItem('progress-read-fault', 'material-progress:ui-test:bio'))
  await page.reload(); await expect(text()).toBeVisible()
  await page.getByRole('button', { name: '放大字号', exact: true }).click()
  await expect(text()).toHaveCSS('font-size', '20px')
  expect(await readPosition()).toEqual({ index: 2, fontSize: 24 })
  await page.evaluate(() => localStorage.removeItem('progress-read-fault'))
  await page.getByRole('button', { name: '重试读取进度', exact: true }).click()
  await expect(text('bio', 3)).toHaveCSS('font-size', '20px')
  await expect.poll(() => readPosition()).toEqual({ index: 2, fontSize: 20 })

  // Continuing navigation during a fault keeps the new local section; restores the untouched font.
  await page.evaluate(() => localStorage.setItem('progress-read-fault', 'material-progress:ui-test:bio'))
  await page.reload(); await expect(text()).toBeVisible()
  await page.getByRole('button', { name: '下一段', exact: true }).click()
  await expect(text('bio', 2)).toBeVisible()
  expect(await readPosition()).toEqual({ index: 2, fontSize: 20 })
  await page.evaluate(() => localStorage.removeItem('progress-read-fault'))
  await page.getByRole('button', { name: '重试读取进度', exact: true }).click()
  await expect(text('bio', 2)).toHaveCSS('font-size', '20px')
  await expect.poll(() => readPosition()).toEqual({ index: 1, fontSize: 20 })

  await page.evaluate(() => localStorage.setItem('progress-write-fault', 'material-progress:ui-test:bio'))
  await page.getByRole('button', { name: '下一段', exact: true }).click()
  await expect(text('bio', 3)).toBeVisible()
  await expect(page.getByText('阅读进度保存失败，当前位置仍保留在页面中。请重试保存。')).toBeVisible()
  expect(await readPosition()).toEqual({ index: 1, fontSize: 20 })
  await screenshot('save-failure')
  await page.evaluate(() => localStorage.removeItem('progress-write-fault'))
  await page.getByRole('button', { name: '重试保存进度', exact: true }).click()
  await expect.poll(() => readPosition()).toEqual({ index: 2, fontSize: 20 })
  await expect(page.getByRole('button', { name: '重试保存进度', exact: true })).toBeHidden()
  // Real button events, rapid navigation, final reload and offline local save.
  await page.getByRole('button', { name: '上一段', exact: true }).click()
  await page.getByRole('button', { name: '上一段', exact: true }).click()
  await page.getByRole('button', { name: '下一段', exact: true }).click()
  await context.setOffline(true)
  await page.getByRole('button', { name: '放大字号', exact: true }).click()
  await expect.poll(() => readPosition()).toEqual({ index: 1, fontSize: 22 })
  await context.setOffline(false)
  await page.reload(); await expect(text('bio', 2)).toHaveCSS('font-size', '22px')

  await page.evaluate(() => { localStorage.setItem('material-progress:ui-test:bio', '{'); localStorage.setItem('progress-write-fault', 'material-recent:ui-test') })
  await page.reload(); await expect(text()).toBeVisible()
  await expect(page.getByRole('button', { name: '重试读取进度', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重试更新最近阅读', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '下一段', exact: true }).click()
  expect(await stored('material-progress:ui-test:bio')).toBe('{')
  await screenshot('corrupt-recent')
  await page.evaluate(() => { localStorage.setItem('material-progress:ui-test:bio', JSON.stringify({ index: 2, fontSize: 24 })); localStorage.removeItem('progress-write-fault') })
  await page.getByRole('button', { name: '重试读取进度', exact: true }).click()
  await expect.poll(() => readPosition()).toEqual({ index: 1, fontSize: 24 })
  await page.getByRole('button', { name: '重试更新最近阅读', exact: true }).click()
  await expect(page.getByRole('button', { name: '重试更新最近阅读', exact: true })).toBeHidden()
  expect(await stored('material-recent:ui-test')).toBe('bio')
  // A corrupt record can be recovered by the reader, without editing storage in developer tools.
  await page.evaluate(() => localStorage.setItem('material-progress:ui-test:bio', '{'))
  await page.reload(); await expect(text()).toBeVisible()
  await page.getByRole('button', { name: '下一段', exact: true }).click()
  await page.getByRole('button', { name: '用当前位置重新保存', exact: true }).click()
  await expect(page.getByText('替换本机阅读进度', { exact: true })).toBeVisible()
  await expect(page.getByText('将记录第 2 段、字号 18。旧阅读位置会被替换，原资料和笔记不受影响。')).toBeVisible()
  await page.getByRole('button', { name: '保留旧记录', exact: true }).click()
  expect(await stored('material-progress:ui-test:bio')).toBe('{')
  await page.getByRole('button', { name: '用当前位置重新保存', exact: true }).click()
  await expect.poll(() => page.getByText('替换本机阅读进度', { exact: true }).evaluate(element => {
    for (let node = element; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 1) return false
    return true
  })).toBeTruthy()
  await screenshot('replace-confirmation')
  await page.evaluate(() => localStorage.setItem('progress-write-fault', 'material-progress:ui-test:bio'))
  await page.getByRole('button', { name: '确认替换进度', exact: true }).click()
  await expect(page.getByText('替换本机阅读进度', { exact: true })).toBeVisible()
  expect(await stored('material-progress:ui-test:bio')).toBe('{')
  await page.evaluate(() => localStorage.removeItem('progress-write-fault'))
  await page.getByRole('button', { name: '确认替换进度', exact: true }).click()
  await expect(page.getByText('替换本机阅读进度', { exact: true })).toBeHidden()
  await expect.poll(() => readPosition()).toEqual({ index: 1, fontSize: 18 })
  await page.reload(); await expect(text('bio', 2)).toHaveCSS('font-size', '18px')
  await page.goto('http://localhost:8096/material?id=history'); await expect(text('history')).toBeVisible()
  await page.getByRole('button', { name: '下一段', exact: true }).click()
  await expect.poll(() => readPosition('ui-test', 'history')).toEqual({ index: 1, fontSize: 18 })
  expect(await stored('material-recent:ui-test')).toBe('history')
  await page.evaluate(() => localStorage.setItem('knowall_user', JSON.stringify({ id: 'other-user', username: '另一用户' })))
  await page.reload(); await expect(text('history')).toBeVisible()
  await page.getByRole('button', { name: '放大字号', exact: true }).click()
  await expect.poll(() => readPosition('other-user', 'history')).toEqual({ index: 0, fontSize: 20 })
  expect(await readPosition('ui-test', 'history')).toEqual({ index: 1, fontSize: 18 })
  expect(await readPosition()).toEqual({ index: 1, fontSize: 18 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(errors).toEqual([]); expect(posts).toEqual([])
  console.log('PASS (' + colorScheme + '/' + width + '): read failure/corruption no overwrite, partial-field restore, local navigation preserved, write retry, rapid page changes, offline save, recent-entry retry, account/document isolation, no overflow/errors/POST.')
} finally { await browser.close() }
