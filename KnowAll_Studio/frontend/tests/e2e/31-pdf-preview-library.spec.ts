import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const evidence = path.resolve('../docs/open-source-integration')
const bytes = (name = 'textbook') => fs.readFileSync(path.join(evidence, 'round-08-fixtures', `${name}.pdf`))
const position = (user: string, id: string) => `knowall:pdf-position:${user}:${id}`
async function source(page: Page, id = 'pdf-textbook', name = 'textbook') {
  const reads: string[] = []
  await page.route(`**/api/v1/documents/${id}`, r => r.fulfill({ json: { id, filename: `${name}.pdf`, file_type: 'pdf', status: 'ready', total_chunks: 1 } }))
  await page.route(`**/api/v1/documents/${id}/chunks`, r => r.fulfill({ json: { chunks: [{ id: 'pdf-section', index: 0, text: 'PDF 学习正文', page_range: 'p1' }] } }))
  await page.route(`**/api/v1/documents/${id}/raw`, r => { reads.push(r.request().headers().authorization); return r.fulfill({ body: bytes(name), contentType: 'application/pdf' }) })
  return reads
}
async function raw(page: Page, name = 'textbook') { await page.route(`**/fixture-${name}.pdf`, r => r.fulfill({ body: bytes(name), contentType: 'application/pdf' })) }
async function show(page: Page, id: string, name = 'textbook') {
  await page.evaluate(async ({ id, name }) => (await import('/.tmp/pdfHarness.tsx')).show(id, `/fixture-${name}.pdf`), { id, name })
}
const pageNumber = (page: Page) => page.getByRole('spinbutton', { name: 'PDF 页码', exact: true })
const text = (page: Page) => page.locator('.react-pdf__Page__textContent')

test('protected textbook uses native outline for keyboard navigation and preserves selected source text', async ({ page }) => {
  const reads = await source(page)
  await page.goto('/materials/pdf-textbook'); await expect(text(page)).toContainText('textbook - Page 1')
  await page.getByRole('link', { name: 'Examples', exact: true }).focus(); await page.keyboard.press('Enter')
  await expect(pageNumber(page)).toHaveValue('3'); await expect(text(page)).toContainText('textbook - Page 3')
  await expect(page.getByRole('button', { name: 'PDF 下一页', exact: true })).toBeDisabled()
  expect(reads).toEqual([`Bearer ${E2E_TOKEN}`])
  expect(await text(page).evaluate(el => getComputedStyle(el).userSelect)).not.toBe('none')
})

test('native internal annotation jumps to an unrendered page', async ({ page }) => {
  await source(page); await page.goto('/materials/pdf-textbook'); await expect(text(page)).toContainText('Jump to examples')
  await page.locator('.react-pdf__Page__annotations .linkAnnotation a').click()
  await expect(pageNumber(page)).toHaveValue('3'); await expect(text(page)).toContainText('textbook - Page 3')
})

test('switching document IDs restores independent positions instead of overwriting the new key', async ({ page }) => {
  await page.goto('/dashboard'); await raw(page)
  await page.evaluate(({ a, b }) => { localStorage.setItem(a, '2'); localStorage.setItem(b, '1') }, { a: position('e2e-user', 'pdf-a'), b: position('e2e-user', 'pdf-b') })
  await show(page, 'pdf-a'); await expect(text(page)).toContainText('Page 2')
  await show(page, 'pdf-b'); await expect(pageNumber(page)).toHaveValue('1'); await expect(text(page)).toContainText('Page 1')
  expect(await page.evaluate(key => localStorage.getItem(key), position('e2e-user', 'pdf-b'))).toBe('1')
  await show(page, 'pdf-a'); await expect(pageNumber(page)).toHaveValue('2')
})

test('changing account restores that account position without copying the previous page', async ({ page }) => {
  await page.goto('/dashboard'); await raw(page)
  await page.evaluate(({ a, b }) => { localStorage.setItem(a, '2'); localStorage.setItem(b, '1') }, { a: position('e2e-user', 'pdf-account'), b: position('next-reader', 'pdf-account') })
  await show(page, 'pdf-account'); await expect(text(page)).toContainText('Page 2')
  await page.evaluate(async () => {
    const { useAuthStore } = await import('/src/stores/index.ts')
    useAuthStore.setState({ user: { ...useAuthStore.getState().user!, id: 'next-reader' } })
  })
  await expect(pageNumber(page)).toHaveValue('1'); await expect(text(page)).toContainText('Page 1')
  expect(await page.evaluate(key => localStorage.getItem(key), position('next-reader', 'pdf-account'))).toBe('1')
})

test('failed replacement disables old page controls and a new healthy file recovers', async ({ page }) => {
  await page.goto('/dashboard'); await raw(page); await raw(page, 'short')
  await page.route('**/fixture-broken.pdf', r => r.fulfill({ body: 'not a pdf', contentType: 'application/pdf' }))
  await show(page, 'pdf-errors'); await expect(text(page)).toContainText('Page 1')
  await show(page, 'pdf-errors', 'broken'); await expect(page.locator('.material-pdf')).toContainText('PDF 加载失败')
  await expect(pageNumber(page)).toBeDisabled()
  await expect(page.getByRole('button', { name: 'PDF 下一页', exact: true })).toBeDisabled()
  await show(page, 'pdf-errors', 'short'); await expect(text(page)).toContainText('short - Page 1')
  await expect(pageNumber(page)).toHaveValue('1')
})

test('Chinese CID-font document loads original local CMaps and readable text without a CDN', async ({ page }) => {
  const logs: string[] = [], resources: string[] = []
  page.on('console', m => { if (m.type() === 'warning' || m.type() === 'error') logs.push(m.text()) })
  page.on('request', r => { if (/bcmap|standard_fonts|\/wasm\//.test(r.url())) resources.push(r.url()) })
  await source(page, 'pdf-cjk', 'cjk')
  try {
    await page.goto('/materials/pdf-cjk')
    await expect(text(page)).toContainText('大学学习资料')
    expect(resources.some(url => /\/pdfjs\/cmaps\/UniGB-UCS2-H.bcmap/.test(url))).toBe(true)
    expect(resources.every(url => new URL(url).origin === new URL(page.url()).origin)).toBe(true)
    expect(logs.some(m => /CMap.*baseUrl|Unable to load.*CMap/.test(m))).toBe(false)
  } finally { fs.writeFileSync(path.join(evidence, `${process.env.OSS_EVIDENCE_PREFIX || 'round-08'}-cjk-${process.env.OSS_EVIDENCE_STAGE || 'current'}.json`), JSON.stringify({ logs, resources }, null, 2)) }
})

test('native font/decoder originals are available from the same-origin resource directory', async ({ page }) => {
  for (const name of ['standard_fonts/LiberationSans-Regular.ttf', 'wasm/openjpeg.wasm', 'wasm/qcms_bg.wasm']) {
    const response = await page.request.get(`/pdfjs/${name}`)
    expect(response.status()).toBe(200)
    expect(Buffer.compare(await response.body(), fs.readFileSync(path.resolve('node_modules/pdfjs-dist', name)))).toBe(0)
  }
})

test('no-outline document remains readable with a clear directory fallback and bounded height', async ({ page }) => {
  await page.goto('/dashboard'); await raw(page, 'plain'); await show(page, 'pdf-plain', 'plain')
  await expect(text(page)).toContainText('plain - Page 1')
  await expect(page.locator('.material-pdf')).toContainText('此文档没有目录')
  await expect(page.locator('.material-pdf-pages')).toHaveCSS('max-height', '321px')
})

test('stale saved page is clamped and persisted only after the short document is loaded', async ({ page }) => {
  await page.goto('/dashboard'); await raw(page, 'short')
  await page.evaluate(key => localStorage.setItem(key, '99'), position('e2e-user', 'pdf-short'))
  await show(page, 'pdf-short', 'short'); await expect(text(page)).toContainText('short - Page 1')
  await expect(pageNumber(page)).toHaveValue('1')
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), position('e2e-user', 'pdf-short'))).toBe('1')
})

for (const status of [401, 204]) test(`raw ${status} retains authorized recovery into the native reader`, async ({ page }) => {
  await source(page); let attempts = 0
  await page.route('**/api/v1/documents/pdf-textbook/raw', r => {
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    return ++attempts === 1 ? r.fulfill({ status }) : r.fulfill({ body: bytes(), contentType: 'application/pdf' })
  })
  await page.goto('/materials/pdf-textbook'); await page.getByRole('button', { name: '重新加载原文件', exact: true }).click()
  await expect(text(page)).toContainText('textbook - Page 1'); expect(attempts).toBe(2)
})

test('switch during a delayed original request cancels it and leaves the new PDF visible', async ({ page }) => {
  await source(page, 'pdf-slow'); await source(page, 'pdf-next', 'short')
  const failures: string[] = []
  page.on('requestfailed', r => { if (r.url().endsWith('/pdf-slow/raw')) failures.push(r.failure()?.errorText || '') })
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/documents/pdf-slow/raw', async r => {
    await held
    await r.fulfill({ body: bytes(), contentType: 'application/pdf' }).catch(() => {})
  })
  await page.goto('/materials/pdf-slow'); await expect(page.getByRole('status').filter({ hasText: '正在打开原文件' })).toBeVisible()
  await page.evaluate(() => { history.pushState({}, '', '/materials/pdf-next'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await expect(text(page)).toContainText('short - Page 1'); release()
  await expect.poll(() => failures.join(' ')).toContain('ERR_ABORTED')
  await expect(pageNumber(page)).toHaveValue('1'); await expect(text(page)).not.toContainText('textbook')
})
