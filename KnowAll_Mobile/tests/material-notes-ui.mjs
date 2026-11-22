// Native Expo project exercised on web with mocked APIs and injected local-storage failures.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const require = createRequire(new URL('../../KnowAll_Studio/frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const colorScheme = process.env.MOBILE_UI_THEME === 'dark' ? 'dark' : 'light'
const width = Number(process.env.MOBILE_UI_WIDTH) || 390
const height = Number(process.env.MOBILE_UI_HEIGHT) || 844
const evidenceRound = process.env.MOBILE_UI_EVIDENCE_ROUND || 'round03'
const context = await browser.newContext({ viewport: { width, height }, colorScheme })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.addInitScript(() => {
  const read = Storage.prototype.getItem
  const write = Storage.prototype.setItem
  Storage.prototype.getItem = function (key) {
    if (key.startsWith('material-note:') && read.call(this, 'test-note-read-fault') === key) throw Error('Injected note read failure')
    return read.call(this, key)
  }
  Storage.prototype.setItem = function (key, value) {
    if (key.startsWith('material-note:') && read.call(this, 'test-note-write-fault') === key) throw Error('Injected note write failure')
    return write.call(this, key, value)
  }
  if (!localStorage.getItem('knowall_token')) {
    localStorage.setItem('knowall_token', 'test-token')
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
  }
})
const documents = ['bio', 'history'].map(id => ({ id, filename: id === 'bio' ? '生物教材.md' : '历史教材.md', file_type: 'md', status: 'ready', subject: id === 'bio' ? '生物' : '历史' }))
await page.route('**/api/**', route => {
  const path = new URL(route.request().url()).pathname
  if (path.endsWith('/documents/')) return route.fulfill({ json: documents })
  const id = path.includes('history') ? 'history' : 'bio'
  if (path.endsWith('/chunks')) return route.fulfill({ json: [{ id: `${id}-1`, index: 0, text: `${id} 正文`, page_range: '1' }] })
  if (path.includes('/documents/') && !path.includes('/learning/')) return route.fulfill({ json: documents.find(doc => doc.id === id) })
  return route.fulfill({ json: null })
})
const openNote = async () => {
  await page.getByRole('button', { name: '当前资料笔记' }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toBeVisible()
}
const field = page.getByRole('textbox', { name: '我的学习笔记' })
const closed = () => expect(page.getByText('这份资料的笔记')).toBeHidden()
try {
  await page.goto('http://localhost:8096')
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible({ timeout: 120000 })
  await page.getByRole('button', { name: '打开学习' }).first().click()
  await openNote()
  await field.fill('关闭时保存，无需手动按保存。')
  await page.getByRole('button', { name: '返回阅读' }).click()
  await closed()
  await page.reload()
  await openNote()
  await expect(field).toHaveValue('关闭时保存，无需手动按保存。')

  await context.setOffline(true)
  await field.fill('停止输入自动保存的内容。')
  await expect(page.getByText('已保存到本机', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('material-note:ui-test:bio'))).toBe('停止输入自动保存的内容。')
  await context.setOffline(false)
  await field.fill('点击遮罩关闭也保存。')
  await page.getByTestId('modal-backdrop').click({ position: { x: 3, y: 3 } })
  await closed()
  await openNote()
  await expect(field).toHaveValue('点击遮罩关闭也保存。')

  await page.evaluate(() => localStorage.setItem('test-note-write-fault', 'material-note:ui-test:bio'))
  await field.fill('失败时要保留的笔记。')
  await page.getByRole('button', { name: '返回阅读' }).click()
  await expect(page.getByText('保存失败，笔记仍在编辑器中。请检查设备存储后重试。')).toBeVisible()
  await expect(field).toHaveValue('失败时要保留的笔记。')
  expect(await page.evaluate(() => localStorage.getItem('material-note:ui-test:bio'))).toBe('点击遮罩关闭也保存。')
  // A route removal must also keep the dirty editor when storage fails.
  await page.evaluate(() => history.back())
  await expect(field).toBeVisible()
  await expect(page).toHaveURL(/\/material/)
  await expect(page.getByText('保存失败，笔记仍在编辑器中。请检查设备存储后重试。')).toBeVisible()
  await expect.poll(() => page.getByTestId('modal-surface').evaluate(element => {
    for (let node = element; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 1) return false
    return true
  })).toBeTruthy()
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${evidenceRound}-note-failure-${colorScheme}-${width}.png`, import.meta.url)), fullPage: true })
  await page.evaluate(() => localStorage.removeItem('test-note-write-fault'))
  await page.getByRole('button', { name: '保存笔记' }).click()
  await expect(page.getByText('已保存到本机', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '返回阅读' }).click()
  await closed()
  await page.getByRole('button', { name: '返回我的资料' }).click()
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '打开学习' }).first().click()
  await openNote()
  await expect(field).toHaveValue('失败时要保留的笔记。')
  // Exercise unsaved route back, rather than only the modal's close handler.
  await field.fill('系统返回前也要保存。')
  await page.evaluate(() => history.back())
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('material-note:ui-test:bio'))).toBe('系统返回前也要保存。')

  await page.evaluate(() => localStorage.setItem('test-note-read-fault', 'material-note:ui-test:bio'))
  await page.getByRole('button', { name: '打开学习' }).first().click()
  await openNote()
  await expect(field).not.toBeEditable()
  await expect(page.getByText('笔记读取失败，原笔记未被覆盖。请重试读取。')).toBeVisible()
  await page.evaluate(() => localStorage.removeItem('test-note-read-fault'))
  await page.getByRole('button', { name: '重试读取笔记' }).click()
  await expect(field).toHaveValue('系统返回前也要保存。')
  await page.getByRole('button', { name: '返回阅读' }).click()
  await closed()

  await page.goto('http://localhost:8096/material?id=history')
  await openNote()
  await expect(field).toHaveValue('')
  await field.fill('历史笔记，不能串入生物。')
  await page.getByRole('button', { name: '返回阅读' }).click()
  await closed()
  await page.evaluate(() => localStorage.setItem('knowall_user', JSON.stringify({ id: 'other-user', username: '另一用户' })))
  await page.reload()
  await openNote()
  await expect(field).toHaveValue('')
  await field.fill('另一用户的历史笔记。')
  await page.getByRole('button', { name: '返回阅读' }).click()
  await closed()
  expect(await page.evaluate(() => localStorage.getItem('material-note:ui-test:history'))).toBe('历史笔记，不能串入生物。')
  expect(await page.evaluate(() => localStorage.getItem('material-note:other-user:history'))).toBe('另一用户的历史笔记。')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
  console.log(`PASS (${colorScheme}): offline note autosave, immediate close, backdrop, route back, save-failure navigation block/retry, read-failure retry, account/document isolation, no overflow or page errors.`)
} finally { await browser.close() }
