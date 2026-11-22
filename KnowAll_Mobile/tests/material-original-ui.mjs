// Expo web supplement: preview bytes come from production backend routes over local HTTP.
// Document list/text and identity/database fixtures are isolated; this is not a native device test.
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
const requests = []
const fixture = process.env.MOBILE_ORIGINAL_FIXTURE_URL || 'http://127.0.0.1:8896'
page.on('pageerror', error => errors.push(error.message))
let failPage = false
let failText = true
let delayed = null
let releaseDelayed = null
let releaseText = null
let textGate = new Promise(resolve => { releaseText = resolve })
await page.addInitScript(() => {
  const read = Storage.prototype.getItem
  const write = Storage.prototype.setItem
  Storage.prototype.getItem = function (key) {
    if (key.startsWith('material-original:') && read.call(this, 'test-original-read-fault') === key) throw Error('Injected read failure')
    return read.call(this, key)
  }
  Storage.prototype.setItem = function (key, value) {
    if (key.startsWith('material-original:') && read.call(this, 'test-original-write-fault') === key) throw Error('Injected write failure')
    return write.call(this, key, value)
  }
  if (!localStorage.getItem('knowall_token')) {
    localStorage.setItem('knowall_token', 'local-ui-test-token')
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'ui-test', username: '测试用户' }))
  }
})
const documents = [{ id: 'bio', filename: '生物原件.pdf', file_type: 'pdf', status: 'ready', subject: '生物' }, { id: 'image', filename: '细胞图像.png', file_type: 'png', status: 'parsing', subject: '生物', material_kind: 'question_bank' }, { id: 'text', filename: '文字资料.md', file_type: 'md', status: 'ready' }]
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  if (path.includes('/preview')) {
    requests.push({ path, auth: route.request().headers()['authorization'] })
    if (failPage && path.endsWith('/image')) return route.fulfill({ status: 404, json: { detail: 'Unavailable fixture' } })
    if (delayed && path.includes('/preview/2/image')) {
      await delayed
      delayed = null
    }
    // Real local HTTP endpoint and actual PDF/Pillow rendering, carrying the app's bearer header.
    try {
      const response = await route.fetch({ url: `${fixture}${path}` })
      return await route.fulfill({ response })
    } catch (error) { if (!String(error).includes('disposed')) throw error }
  }
  if (path.endsWith('/documents/')) return route.fulfill({ json: documents })
  const id = path.includes('image') ? 'image' : path.includes('/documents/text') ? 'text' : 'bio'
  if (path.endsWith('/learning/documents/image/pack')) return route.fulfill({ json: { id: 'image-pack', sections: [{ id: 'image-section', title: '细胞结构', notes: '图像资料对应的学习内容。', chunk_ids: ['image-1'], flashcards: [] }] } })
  if (path.endsWith('/chunks')) {
    if (id === 'bio' && textGate) await textGate
    return failText ? route.fulfill({ status: 400, json: { detail: 'Text unavailable' } }) : route.fulfill({ json: [{ id: `${id}-1`, index: 0, text: `${id} 正文与来源`, page_range: '1' }] })
  }
  if (path.includes('/documents/') && !path.includes('/learning/')) return route.fulfill({ json: documents.find(doc => doc.id === id) })
  return route.fulfill({ json: null })
})
const image = n => page.getByRole('img', { name: `原件第${n}页`, exact: true })
const ready = async n => { await expect(image(n)).toBeVisible({ timeout: 10000 }); await expect.poll(() => image(n).evaluate(e => e.naturalWidth)).toBeGreaterThan(0) }
const position = () => page.evaluate(() => JSON.parse(localStorage.getItem('material-original:ui-test:bio') || 'null'))
const back = () => page.getByRole('button', { name: '返回我的资料' }).click()
const open = async n => { await page.getByRole('button', { name: '打开学习' }).nth(n).click(); await ready(1) }
try {
  await page.goto('http://localhost:8096')
  await expect(page.getByText('我的资料', { exact: true }).first()).toBeVisible({ timeout: 120000 })
  await open(0)
  releaseText(); textGate = null
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '文字', exact: true }).click()
  await expect(page.getByText('文字加载失败，原件仍可阅读，请重试文字加载。')).toBeVisible()
  await page.getByRole('button', { name: '原件', exact: true }).click()
  await ready(1)
  const firstUri = await image(1).getAttribute('src')
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await ready(2)
  expect(await image(2).getAttribute('src')).not.toBe(firstUri)
  await page.getByRole('button', { name: '放大原件' }).click()
  await expect.poll(position).toEqual({ page: 2, zoom: 1.5 })
  await expect.poll(() => image(2).evaluate(e => e.getBoundingClientRect().width)).toBeGreaterThan(width)
  await page.reload()
  await ready(2)
  await expect(page.getByText('原件 · 150% · 可横向拖动', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '适宽', exact: true }).click()
  await page.getByRole('textbox', { name: '跳转页码' }).fill('9')
  await page.getByRole('button', { name: '跳转', exact: true }).click()
  await expect(page.getByText('请输入1至2的页码')).toBeVisible()
  await page.getByRole('textbox', { name: '跳转页码' }).fill('1')
  await page.getByRole('button', { name: '跳转', exact: true }).click()
  await ready(1)
  await expect.poll(position).toEqual({ page: 1, zoom: 1 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${process.env.MOBILE_UI_EVIDENCE_ROUND || 'round04'}-original-${colorScheme}-${width}.png`, import.meta.url)), fullPage: true })
  failPage = true
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByText('原文件暂不可用，请确认已上传并有访问权限')).toBeVisible()
  expect(await position()).toEqual({ page: 1, zoom: 1 }) // Failed pages never replace the last successfully read page.
  failPage = false
  await page.getByRole('button', { name: '重试原件', exact: true }).click()
  await ready(2)
  await expect.poll(position).toEqual({ page: 2, zoom: 1 })

  await page.evaluate(() => localStorage.setItem('test-original-write-fault', 'material-original:ui-test:bio'))
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await ready(1)
  await expect(page.getByText('原件进度保存失败，请检查设备存储')).toBeVisible()
  expect(await position()).toEqual({ page: 2, zoom: 1 })
  await page.evaluate(() => localStorage.removeItem('test-original-write-fault'))
  await page.getByRole('button', { name: '重试保存', exact: true }).click()
  await expect.poll(position).toEqual({ page: 1, zoom: 1 })
  await page.evaluate(() => localStorage.setItem('test-original-read-fault', 'material-original:ui-test:bio'))
  await page.reload()
  await ready(1)
  await expect(page.getByText('进度读取失败，本次未覆盖已有进度')).toBeVisible()
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await ready(2)
  await page.evaluate(() => localStorage.removeItem('test-original-read-fault'))
  expect(await position()).toEqual({ page: 1, zoom: 1 })
  await page.getByRole('button', { name: '重试读取', exact: true }).click()
  await ready(2)
  await expect.poll(position).toEqual({ page: 2, zoom: 1 })
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await ready(1)
  await expect.poll(position).toEqual({ page: 1, zoom: 1 })

  // Corrupt JSON can be recovered by the user, without silently replacing its original bytes.
  await page.evaluate(() => localStorage.setItem('material-original:ui-test:bio', '{'))
  await page.reload(); await ready(1)
  await expect(page.getByText('进度读取失败，本次未覆盖已有进度')).toBeVisible()
  await page.getByRole('button', { name: '下一页', exact: true }).click(); await ready(2)
  await page.getByRole('button', { name: '放大原件', exact: true }).click()
  expect(await page.evaluate(() => localStorage.getItem('material-original:ui-test:bio'))).toBe('{')
  await page.getByRole('button', { name: '用当前原件位置保存', exact: true }).click()
  await expect(page.getByText('将记录第 2 页、缩放 150%。旧原件位置会被替换，原文件和笔记不受影响。')).toBeVisible()
  await page.getByRole('button', { name: '保留原件旧记录', exact: true }).click()
  expect(await page.evaluate(() => localStorage.getItem('material-original:ui-test:bio'))).toBe('{')
  await page.getByRole('button', { name: '用当前原件位置保存', exact: true }).click()
  await expect.poll(() => page.getByText('替换原件阅读进度', { exact: true }).evaluate(element => {
    for (let node = element; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 1) return false
    return true
  })).toBeTruthy()
  await page.screenshot({ path: fileURLToPath(new URL(`../docs/patrol/${process.env.MOBILE_UI_EVIDENCE_ROUND || 'round09'}-original-confirm-${colorScheme}-${width}.png`, import.meta.url)), fullPage: true })
  await page.evaluate(() => localStorage.setItem('test-original-write-fault', 'material-original:ui-test:bio'))
  await page.getByRole('button', { name: '确认保存原件位置', exact: true }).click()
  await expect(page.getByText('替换原件阅读进度', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('material-original:ui-test:bio'))).toBe('{')
  await page.evaluate(() => localStorage.removeItem('test-original-write-fault'))
  await page.getByRole('button', { name: '确认保存原件位置', exact: true }).click()
  await expect(page.getByText('替换原件阅读进度', { exact: true })).toBeHidden()
  await expect.poll(position).toEqual({ page: 2, zoom: 1.5 })
  await page.reload(); await ready(2)
  // Unmount and immediately reopen the original reader via the parsed-text view.
  await page.getByRole('button', { name: '放大原件', exact: true }).click()
  await page.getByRole('button', { name: '文字', exact: true }).click()
  await page.getByRole('button', { name: '原件', exact: true }).click()
  await ready(2); await expect.poll(position).toEqual({ page: 2, zoom: 2 })
  await expect(page.getByText('原件 · 200% · 可横向拖动', { exact: true })).toBeVisible()
  // Only changing zoom during a read fault retains the saved page on a successful retry.
  await page.evaluate(() => localStorage.setItem('test-original-read-fault', 'material-original:ui-test:bio'))
  await page.reload(); await ready(1)
  await page.getByRole('button', { name: '放大原件', exact: true }).click()
  await page.evaluate(() => localStorage.removeItem('test-original-read-fault'))
  expect(await position()).toEqual({ page: 2, zoom: 2 })
  await page.getByRole('button', { name: '重试读取', exact: true }).click()
  await ready(2); await expect.poll(position).toEqual({ page: 2, zoom: 1.5 })
  await page.getByRole('button', { name: '适宽', exact: true }).click()
  await page.getByRole('button', { name: '上一页', exact: true }).click(); await ready(1)
  await expect.poll(position).toEqual({ page: 1, zoom: 1 })

  // Switch away while an old PDF page response is waiting; the image document cannot display it.
  delayed = new Promise(resolve => { releaseDelayed = resolve })
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByLabel('正在加载原件')).toBeVisible()
  await back()
  await open(1)
  const imageUri = await image(1).getAttribute('src')
  releaseDelayed()
  await expect.poll(() => page.getByRole('img', { name: /^原件第/ }).count()).toBe(1)
  expect(await image(1).getAttribute('src')).toBe(imageUri)
  expect(await position()).toEqual({ page: 1, zoom: 1 })
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeHidden()
  failText = false
  await page.getByRole('button', { name: '文字', exact: true }).click()
  await page.getByRole('button', { name: '重新加载正文' }).click()
  await ready(1)
  await page.getByRole('button', { name: '文字', exact: true }).click()
  await expect(page.getByText('image 正文与来源', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '原件', exact: true }).click()
  await ready(1)
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await expect(page.getByText('图像资料对应的学习内容。')).toBeVisible()
  await page.getByRole('button', { name: '阅读对应原文' }).click()
  await expect(page.getByText('image 正文与来源', { exact: true })).toBeVisible()
  await expect(page.getByText('当前资料学习工具')).toBeHidden()
  failText = true
  await back()
  await page.getByRole('button', { name: '打开学习' }).nth(2).click()
  await expect(page.getByText('文字加载失败，请检查网络后重试。')).toBeVisible()
  await expect(page.getByRole('button', { name: '原件', exact: true })).toBeHidden()
  await back()
  await open(1)

  // A foreign account receives a genuine 404 from the production permission guard.
  await page.evaluate(() => {
    localStorage.setItem('knowall_token', 'other-account-token')
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'other', username: '另一个账号' }))
  })
  await page.reload()
  await expect(page.getByText('原文件暂不可用，请确认已上传并有访问权限')).toBeVisible()
  await expect(page.getByRole('img', { name: /^原件第/ })).toBeHidden()
  expect(await page.evaluate(() => localStorage.getItem('material-original:other:image'))).toBeNull()
  expect(requests.filter(r => r.auth === 'Bearer local-ui-test-token').length).toBeGreaterThan(5)
  expect(requests.some(r => r.auth === 'Bearer other-account-token')).toBeTruthy()
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  console.log(`PASS (${colorScheme}, ${width}px): real HTTP original PDF/image bytes, bearer/owner guard, text failure fallback, paging/jump/zoom/restore, fetch retry, read/write failure, cancelled stale page, document/account isolation; no overflow or page errors.`)
} finally { releaseText?.(); releaseDelayed?.(); await browser.close() }
