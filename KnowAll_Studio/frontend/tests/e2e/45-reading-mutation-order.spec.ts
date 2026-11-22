import { test, expect } from './fixtures'
import type { Page, Route } from '@playwright/test'

const content = '我们通过认真学习理解这个世界，并且每天阅读新的知识内容。'
const oldArticle = { id: 'u1', title: '旧上传文章', content: '旧请求' + content, difficulty: 0, tags: [], source: 'upload' }
const newArticle = { id: 2, title: '后来选择文章', content: '新请求' + content, difficulty: 1, tags: [] }
const uploadArticle = { id: 'u3', title: '保存的文章', content: '保存请求' + content, difficulty: 0, tags: [], source: 'upload' }
const result = (old = false) => ({ done: true, result: old ? '旧结果 oldword' : '新结果 newword', vocabulary: [{ cn: old ? '旧词' : '新词', en: old ? 'oldword' : 'newword' }] })
const item = (page: Page, title: string) => page.locator('.reading-article-item').filter({ hasText: title })
const modal = (page: Page) => page.getByRole('dialog').filter({ hasText: '上传文章' })
const saved = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
const settle = (page: Page) => page.evaluate(() => new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
let releases: (() => void)[] = []
function gate() {
  let release!: () => void
  const promise = new Promise<void>(resolve => { release = resolve })
  releases.push(release)
  return { promise, release }
}
async function openUpload(page: Page, title = uploadArticle.title, body = uploadArticle.content) {
  await page.getByRole('button', { name: '+ 上传文章', exact: true }).click()
  await modal(page).getByPlaceholder('文章标题（必填）').fill(title)
  await modal(page).getByPlaceholder('文章正文...').fill(body)
}
async function delayedFile(page: Page) {
  await page.addInitScript(() => {
    const original = File.prototype.arrayBuffer
    ;(window as any).fileGates = {}
    File.prototype.arrayBuffer = function () {
      if (!this.name.startsWith('slow')) return original.call(this)
      const file = this
      return new Promise(resolve => { (window as any).fileGates[this.name] = async () => resolve(await original.call(file)) })
    }
  })
}
async function releaseFile(page: Page, name: string) {
  await expect.poll(() => page.evaluate(name => !!(window as any).fileGates[name], name)).toBe(true)
  await page.evaluate(name => (window as any).fileGates[name](), name)
  await settle(page)
}
async function deferUpload(page: Page) {
  const wait = gate(); const requests: any[] = []
  await page.route('**/api/v1/reading/articles/upload', async r => {
    requests.push(r.request().postDataJSON()); await wait.promise
    await r.fulfill({ json: { ok: true, article: uploadArticle } }).catch(() => {})
  })
  return { requests, release: wait.release }
}
test.beforeEach(async ({ page }) => {
  releases = []
  await page.route('**/api/v1/reading/articles*', r => r.fulfill({ json: [oldArticle, newArticle] }))
  await page.route('**/api/v1/reading/articles/u1', r => r.fulfill({ json: oldArticle }))
  await page.route('**/api/v1/reading/articles/2', r => r.fulfill({ json: newArticle }))
  await page.route('**/api/v1/reading/convert/stream', r => r.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify(result(r.request().postDataJSON().text.includes('旧请求')))}\n\n` }))
})
test.afterEach(async () => { releases.forEach(release => release()) })

test('TXT CRLF first line becomes title and only remaining lines become body', async ({ page }) => {
  await page.goto('/reading'); await openUpload(page)
  await modal(page).locator('input[type=file]').setInputFiles({ name: 'reading.txt', mimeType: 'text/plain', buffer: Buffer.from('文件标题\r\n' + content + '\r\n第二段正文') })
  await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue('文件标题')
  // Textarea DOM normalizes CRLF; the native SQL test separately checks bytes.
  await expect(modal(page).getByPlaceholder('文章正文...')).toHaveValue(content + '\n第二段正文')
})

test('late local file read cannot replace manually edited draft', async ({ page }) => {
  await delayedFile(page); await page.goto('/reading'); await openUpload(page)
  await modal(page).locator('input[type=file]').setInputFiles({ name: 'slow-edit.txt', mimeType: 'text/plain', buffer: Buffer.from('旧文件标题\n' + content) })
  await modal(page).getByPlaceholder('文章标题（必填）').fill('手动修改标题')
  await modal(page).getByPlaceholder('文章正文...').fill('手动修改正文')
  await releaseFile(page, 'slow-edit.txt')
  await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue('手动修改标题')
  await expect(modal(page).getByPlaceholder('文章正文...')).toHaveValue('手动修改正文')
})

test('late old file cannot replace newer selected file', async ({ page }) => {
  await delayedFile(page); await page.goto('/reading'); await openUpload(page)
  await modal(page).locator('input[type=file]').setInputFiles({ name: 'slow-old.txt', mimeType: 'text/plain', buffer: Buffer.from('旧文件标题\n' + content) })
  await modal(page).locator('input[type=file]').setInputFiles({ name: 'new.txt', mimeType: 'text/plain', buffer: Buffer.from('新文件标题\n' + content) })
  await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue('新文件标题')
  await releaseFile(page, 'slow-old.txt')
  await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue('新文件标题')
})

test('file finishing after dialog close cannot replace reopened draft', async ({ page }) => {
  await delayedFile(page); await page.goto('/reading'); await openUpload(page)
  await modal(page).locator('input[type=file]').setInputFiles({ name: 'slow-close.txt', mimeType: 'text/plain', buffer: Buffer.from('旧文件标题\n' + content) })
  await modal(page).getByRole('button', { name: '取消', exact: true }).click()
  await openUpload(page, '新草稿标题', '新草稿正文')
  await releaseFile(page, 'slow-close.txt')
  await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue('新草稿标题')
})

test('pending save is disabled and duplicate clicks send one write', async ({ page }) => {
  const pending = await deferUpload(page)
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).dblclick()
  await expect.poll(() => pending.requests.length).toBe(1)
  await expect(modal(page).getByRole('button', { name: /保存/ })).toBeDisabled()
  pending.release(); await expect(page.locator('.reading-text')).toContainText('新结果')
  expect((await saved(page)).totalRead).toBe(1)
})

test('late saved article cannot replace a newer article selection', async ({ page }) => {
  const pending = await deferUpload(page); let conversions = 0
  page.on('request', r => { if (r.url().endsWith('/reading/convert/stream')) conversions++ })
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => pending.requests.length).toBe(1)
  await modal(page).getByRole('button', { name: '取消', exact: true }).click()
  await item(page, newArticle.title).click(); await expect(page.locator('.reading-text')).toContainText('新结果')
  pending.release(); await expect(page.getByText('文章已保存！', { exact: true })).toBeVisible(); await settle(page)
  await expect(page.locator('.reading-main-header h2')).toContainText(newArticle.title)
  expect(conversions).toBe(1)
  expect((await saved(page)).totalRead).toBe(1)
})

test('editing draft while saving preserves the newer input after response', async ({ page }) => {
  const pending = await deferUpload(page)
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => pending.requests.length).toBe(1)
  await modal(page).getByPlaceholder('文章标题（必填）').fill('新草稿标题')
  await modal(page).getByPlaceholder('文章正文...').fill('新草稿正文')
  pending.release(); await expect(page.getByText('文章已保存！', { exact: true })).toBeVisible(); await settle(page)
  await expect(modal(page)).toBeVisible()
  await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue('新草稿标题')
  await expect(modal(page).getByPlaceholder('文章正文...')).toHaveValue('新草稿正文')
  expect(await saved(page)).toEqual({})
})

test('upload failure preserves draft and permits a successful retry', async ({ page }) => {
  let attempts = 0
  await page.route('**/api/v1/reading/articles/upload', r => {
    attempts++
    return attempts === 1 ? r.fulfill({ status: 400, json: { detail: '模拟校验失败' } }) : r.fulfill({ json: { ok: true, article: uploadArticle } })
  })
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('上传失败: 模拟校验失败', { exact: true })).toBeVisible()
  await expect(modal(page).getByPlaceholder('文章正文...')).toHaveValue(uploadArticle.content)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.reading-main-header h2')).toContainText(uploadArticle.title)
  await expect(page.locator('.reading-text')).toContainText('新结果')
  expect(attempts).toBe(2); expect((await saved(page)).totalRead).toBe(1)
})

test('save lock ends after the write even while automatic reading is pending', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      if (!String(input).endsWith('/reading/convert/stream')) return original(input, init)
      return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"token":"明确模拟尚在转换"}\n\n')) } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
  })
  await page.route('**/api/v1/reading/articles/upload', r => r.fulfill({ json: { ok: true, article: uploadArticle } }))
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect(modal(page)).toBeHidden()
  await expect(page.getByText('⏳ 正在生成...')).toBeVisible()
  await openUpload(page, '下一篇草稿', content)
  await expect(modal(page).getByRole('button', { name: /保存/ })).toBeEnabled()
})

test('late deletion of old selection does not clear newer reading', async ({ page }) => {
  const wait = gate(); let arrived = false
  await page.route('**/api/v1/reading/articles/u1', async r => {
    if (r.request().method() !== 'DELETE') return r.fulfill({ json: oldArticle })
    arrived = true; await wait.promise; await r.fulfill({ json: { ok: true } }).catch(() => {})
  })
  await page.goto('/reading'); await item(page, oldArticle.title).click()
  await expect(page.locator('.reading-text')).toContainText('旧结果')
  await item(page, oldArticle.title).getByTitle('删除', { exact: true }).click()
  await page.getByRole('button', { name: /删\s*除/ }).click()
  await expect.poll(() => arrived).toBe(true)
  await page.getByRole('dialog').filter({ hasText: '确定删除这篇文章' }).getByRole('button', { name: /取\s*消/ }).click()
  await item(page, newArticle.title).click(); await expect(page.locator('.reading-text')).toContainText('新结果')
  wait.release(); await expect(page.getByText('已删除', { exact: true })).toBeVisible(); await settle(page)
  await expect(page.locator('.reading-main-header h2')).toContainText(newArticle.title)
  await expect(page.locator('.reading-text')).toContainText('新结果')
  expect((await saved(page)).totalRead).toBe(2)
})

test('deleting current pending stream aborts it and cannot resurrect deleted vocabulary', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    ;(window as any).deletedReadingAborted = false
    window.fetch = async (input, init) => {
      if (!String(input).endsWith('/reading/convert/stream') || !String(init?.body).includes('旧请求')) return original(input, init)
      init?.signal?.addEventListener('abort', () => { (window as any).deletedReadingAborted = true })
      return new Response(new ReadableStream({ start(c) {
        c.enqueue(new TextEncoder().encode('data: {"token":"旧片段"}\n\n'))
        ;(window as any).finishDeletedReading = () => { try { c.enqueue(new TextEncoder().encode('data: {"done":true,"result":"旧结果 oldword","vocabulary":[{"cn":"旧词","en":"oldword"}]}\n\n')); c.close() } catch { /* canceled reader */ } }
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
  })
  await page.route('**/api/v1/reading/articles/u1', r => r.fulfill({ json: r.request().method() === 'DELETE' ? { ok: true } : oldArticle }))
  await page.goto('/reading'); await item(page, oldArticle.title).click()
  await expect.poll(() => page.evaluate(() => !!(window as any).finishDeletedReading)).toBe(true)
  await item(page, oldArticle.title).getByTitle('删除', { exact: true }).click()
  await page.getByRole('button', { name: /删\s*除/ }).click()
  await expect(page.getByText('已删除', { exact: true })).toBeVisible()
  await page.evaluate(() => (window as any).finishDeletedReading()); await settle(page)
  expect(await page.evaluate(() => (window as any).deletedReadingAborted)).toBe(true)
  expect(await saved(page)).toEqual({})
  await expect(page.getByRole('button', { name: /换一批词/ })).toBeEnabled()
  await expect(page.locator('.reading-text')).toHaveCount(0)
  await item(page, newArticle.title).click(); await expect(page.locator('.reading-text')).toContainText('新结果')
  expect((await saved(page)).totalRead).toBe(1)
})

test('upload completing after unmount cannot start conversion or persist reading', async ({ page }) => {
  const pending = await deferUpload(page); let conversions = 0
  page.on('request', r => { if (r.url().endsWith('/reading/convert/stream')) conversions++ })
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => pending.requests.length).toBe(1)
  await page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.locator('.reading-main')).toHaveCount(0)
  pending.release(); await settle(page)
  expect(conversions).toBe(0); expect(await saved(page)).toEqual({})
})

test('older list refresh cannot undo later successful deletion list', async ({ page }) => {
  const wait = gate(); let lists = 0
  await page.route('**/api/v1/reading/articles', async r => {
    lists++
    if (lists === 2) { await wait.promise; await r.fulfill({ json: [oldArticle, newArticle, uploadArticle] }).catch(() => {}); return }
    await r.fulfill({ json: lists >= 3 ? [newArticle, uploadArticle] : [oldArticle, newArticle] })
  })
  await page.route('**/api/v1/reading/articles/upload', r => r.fulfill({ json: { ok: true, article: uploadArticle } }))
  await page.route('**/api/v1/reading/articles/u1', r => r.fulfill({ json: r.request().method() === 'DELETE' ? { ok: true } : oldArticle }))
  await page.goto('/reading'); await openUpload(page)
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => lists).toBe(2)
  // Both old/new product may close upload before or after the list. Cancel if still visible.
  if (await modal(page).isVisible()) await modal(page).getByRole('button', { name: '取消', exact: true }).evaluate(button => (button as HTMLButtonElement).click())
  await expect(modal(page)).toBeHidden()
  await item(page, oldArticle.title).getByTitle('删除', { exact: true }).click()
  await page.getByRole('button', { name: /删\s*除/ }).click()
  await expect.poll(() => lists).toBe(3); await expect(item(page, oldArticle.title)).toHaveCount(0)
  wait.release(); await settle(page)
  await expect(item(page, oldArticle.title)).toHaveCount(0)
})

test('delete completing after unmount does not refresh removed page', async ({ page }) => {
  const wait = gate(); let deleting = false; let lists = 0
  await page.route('**/api/v1/reading/articles', r => { lists++; return r.fulfill({ json: [oldArticle, newArticle] }) })
  await page.route('**/api/v1/reading/articles/u1', async r => {
    deleting = true; await wait.promise; await r.fulfill({ json: { ok: true } }).catch(() => {})
  })
  await page.goto('/reading'); await item(page, oldArticle.title).getByTitle('删除', { exact: true }).click()
  await page.getByRole('button', { name: /删\s*除/ }).click(); await expect.poll(() => deleting).toBe(true)
  await page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.locator('.reading-main')).toHaveCount(0)
  wait.release(); await settle(page)
  expect(lists).toBe(1)
})

test('title-only TXT is rejected without sending a write', async ({ page }) => {
  let uploads = 0
  page.on('request', r => { if (r.url().endsWith('/reading/articles/upload')) uploads++ })
  await page.goto('/reading'); await openUpload(page, '', '')
  await modal(page).locator('input[type=file]').setInputFiles({ name: 'empty.txt', mimeType: 'text/plain', buffer: Buffer.from('只有标题') })
  await expect(page.getByText('文件格式错误：首行为标题，之后为正文', { exact: true })).toBeVisible()
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('请输入标题和内容', { exact: true })).toBeVisible()
  expect(uploads).toBe(0)
})

