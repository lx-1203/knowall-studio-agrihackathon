import { test, expect, E2E_TOKEN } from './fixtures'
const file = { name: '账号资料.md', mimeType: 'text/markdown', buffer: Buffer.from('# 原始资料\n保留上传原文') }
test.beforeEach(async ({ page }) => { await page.setViewportSize({ width: 1600, height: 950 }) })
async function openFiles(page: any, legacy = false) {
  await page.goto(legacy ? '/upload' : '/materials')
  if (!legacy) await page.getByRole('button', { name: /上传资料$/ }).first().click()
}

test('URL import sends current bearer and preserves exact URL/body', async ({ page }) => {
  let headers: any; let body: any; let count = 0
  await page.route('**/api/v1/documents/import-url', r => { count++; headers = r.request().headers(); body = r.request().postDataJSON(); return r.fulfill({ json: { document_id: 'url-doc', total_chunks: 3 } }) })
  await page.goto('/upload'); await page.getByRole('button', { name: /导入URL$/ }).click()
  await page.getByPlaceholder('https://example.com/article').fill('https://example.test/中文?a=1&b=%20')
  await page.getByRole('button', { name: /导\s*入$/, exact: true }).click(); await expect(page.getByText(/URL导入成功/)).toBeVisible()
  expect(headers.authorization).toBe(`Bearer ${E2E_TOKEN}`); expect(body).toEqual({ url: 'https://example.test/中文?a=1&b=%20' }); expect(count).toBe(1)
})

test('URL 401 retains the existing account logout', async ({ page }) => {
  await page.route('**/api/v1/documents/import-url', r => r.fulfill({ status: 401, json: { detail: 'expired' } }))
  await page.goto('/upload'); await page.getByRole('button', { name: /导入URL$/ }).click()
  await page.getByPlaceholder('https://example.com/article').fill('https://example.test/article')
  await page.getByRole('button', { name: /导\s*入$/, exact: true }).click(); await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
})

test('URL failure is persistent, keeps input, and retries only on explicit submit', async ({ page }) => {
  let ready = false; let count = 0
  await page.route('**/api/v1/documents/import-url', r => { count++; return ready ? r.fulfill({ json: { document_id: 'url-doc', total_chunks: 3 } }) : r.fulfill({ status: 503, json: { detail: 'temporary import failure' } }) })
  await page.goto('/upload'); await page.getByRole('button', { name: /导入URL$/ }).click(); const input = page.getByPlaceholder('https://example.com/article'); await input.fill('https://example.test/article')
  await page.getByRole('button', { name: /导\s*入$/, exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('导入失败'); await expect(input).toHaveValue('https://example.test/article'); expect(count).toBe(1)
  ready = true; await page.getByRole('button', { name: /导\s*入$/, exact: true }).click(); await expect(page.getByRole('dialog')).toBeHidden(); expect(count).toBe(2)
})

test('rapid URL submit sends one write and closing pending import aborts the wait', async ({ page }) => {
  await page.addInitScript(() => {
    const original = XMLHttpRequest.prototype.abort
    ;(window as any).__nativeAbortCount = 0
    XMLHttpRequest.prototype.abort = function () { (window as any).__nativeAbortCount++; return original.call(this) }
  })
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let count = 0; let aborted = false
  page.on('requestfailed', r => { if (r.url().endsWith('/documents/import-url')) aborted = true })
  await page.route('**/api/v1/documents/import-url', async r => { count++; await gate; await r.fulfill({ json: { document_id: 'late-doc' } }).catch(() => {}) })
  await page.goto('/upload'); await page.getByRole('button', { name: /导入URL$/ }).click(); await page.getByPlaceholder('https://example.com/article').fill('https://example.test/article')
  await page.getByRole('button', { name: /导\s*入$/, exact: true }).evaluate((el: HTMLElement) => { el.click(); el.click() })
  await expect.poll(() => count).toBe(1); await page.getByRole('dialog').getByRole('button', { name: /取\s*消$/ }).click()
  await expect(page.getByRole('dialog')).toBeHidden()
  expect(await page.evaluate(() => (window as any).__nativeAbortCount)).toBe(1)
  release(); await expect.poll(() => aborted).toBe(true); await expect(page.getByText(/URL导入成功/)).toBeHidden()
})

for (const legacy of [false, true]) {
  test(`${legacy ? 'advanced' : 'primary'} native progress distinguishes unknown total and transferred bytes from parsing`, async ({ page }) => {
    // Deterministic native ProgressEvent fixture; the packaged smoke separately transfers real throttled bytes.
    await page.addInitScript(() => {
      const open = XMLHttpRequest.prototype.open
      XMLHttpRequest.prototype.open = function (...args: any[]) {
        if (String(args[1]).includes('/documents/upload')) {
          ;(window as any).__uploadXHR = this
          const add = this.upload.addEventListener.bind(this.upload)
          this.upload.addEventListener = ((type: string, listener: EventListener, options?: any) =>
            add(type, type === 'progress' ? (event: Event) => { if (!event.isTrusted) listener(event) } : listener, options)) as any
        }
        return (open as any).apply(this, args)
      }
    })
    let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve })
    await page.route('**/api/v1/documents/upload', async r => { await gate; await r.fulfill({ status: 503, json: {} }).catch(() => {}) })
    await openFiles(page, legacy); await page.locator('input[type=file]').setInputFiles(file)
    await expect(page.getByRole('button', { name: '停止当前上传', exact: true })).toBeVisible()
    await page.evaluate(() => (window as any).__uploadXHR.upload.dispatchEvent(new ProgressEvent('progress', { loaded: 100, lengthComputable: false })))
    await expect(page.getByText('正在传输文件...', { exact: true })).toBeVisible()
    await expect(page.getByLabel('文件上传进度', { exact: true })).toBeHidden()
    await page.evaluate(() => (window as any).__uploadXHR.upload.dispatchEvent(new ProgressEvent('progress', { loaded: 350, total: 1000, lengthComputable: true })))
    await expect(page.getByLabel('文件上传进度', { exact: true })).toContainText('35% 已传输')
    await page.evaluate(() => (window as any).__uploadXHR.upload.dispatchEvent(new ProgressEvent('progress', { loaded: 1000, total: 1000, lengthComputable: true })))
    await expect(page.getByText('文件已传输，正在等待解析结果。', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '停止当前上传', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '停止当前上传', exact: true }).click(); release()
  })
}

test('advanced cancellation during indexing resets the completed source spinner', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let indexing = false
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'index-doc', filename: file.name, status: 'ready' } }))
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ json: [{ id: 'index-doc', filename: file.name, file_type: 'md', status: 'ready' }] }))
  await page.route('**/api/v1/search/index?*', async r => { indexing = true; await gate; await r.fulfill({ json: { indexed: 1 } }).catch(() => {}) })
  await openFiles(page, true); await page.locator('input[type=file]').setInputFiles(file)
  await expect.poll(() => indexing).toBe(true)
  const button = page.getByRole('button', { name: /索引$/ })
  await expect(button).toHaveClass(/ant-btn-loading/)
  await page.getByRole('button', { name: '停止当前上传', exact: true }).click()
  await expect(button).not.toHaveClass(/ant-btn-loading/); release()
})

for (const legacy of [false, true]) {
  test(`${legacy ? 'advanced' : 'primary'} upload cancels waiting, creates no follow-up and can recover`, async ({ page }) => {
    let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let count = 0; let followups = 0; let aborted = false
    page.on('requestfailed', r => { if (r.url().endsWith('/documents/upload')) aborted = true })
    await page.route('**/api/v1/documents/upload', async r => { count++; expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`); expect(r.request().headers()['content-type']).toContain('multipart/form-data; boundary='); expect(r.request().postDataBuffer()?.toString()).toContain('保留上传原文'); await gate; await r.fulfill({ json: { document_id: 'late', filename: file.name, total_chunks: 1 } }).catch(() => {}) })
    page.on('request', r => { if (/\/organization|\/search\/index/.test(r.url())) followups++ })
    await openFiles(page, legacy); await page.locator('input[type=file]').setInputFiles(file)
    await expect.poll(() => count).toBe(1); await page.getByRole('button', { name: '停止当前上传', exact: true }).click()
    await expect.poll(() => aborted).toBe(true); await expect(page.getByText(/已停止等待/)).toBeVisible()
    release(); expect(followups).toBe(0); await expect(page).toHaveURL(new RegExp(legacy ? '/upload$' : '/materials$'))
    await expect(page.locator('input[type=file]')).toBeEnabled(); await expect(page.getByText(/上传失败/)).toBeHidden()
    await page.route('**/api/v1/documents/upload', r => r.fulfill({ status: 503, json: { detail: 'temporary upload failure' } }))
    await page.locator('input[type=file]').setInputFiles(file); await expect(page.getByRole('alert').filter({ hasText: '上传失败' })).toBeVisible()
  })

  test(`${legacy ? 'advanced' : 'primary'} leaving upload aborts pending HTTP and avoids late actions`, async ({ page }) => {
    let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let started = false; let aborted = false; let followups = 0
    page.on('requestfailed', r => { if (r.url().endsWith('/documents/upload')) aborted = true })
    page.on('request', r => { if (/\/organization|\/search\/index/.test(r.url())) followups++ })
    await page.route('**/api/v1/documents/upload', async r => { started = true; await gate; await r.fulfill({ json: { document_id: 'late', filename: file.name } }).catch(() => {}) })
    await openFiles(page, legacy); await page.locator('input[type=file]').setInputFiles(file); await expect.poll(() => started).toBe(true)
    await page.evaluate(() => { history.pushState(null, '', '/summary/no-source'); dispatchEvent(new PopStateEvent('popstate')) })
    await expect.poll(() => aborted).toBe(true); release(); expect(followups).toBe(0); await expect(page.getByText(/上传成功|上传失败/)).toBeHidden()
  })

  test(`${legacy ? 'advanced' : 'primary'} multiple selections cannot create concurrent upload requests`, async ({ page }) => {
    let count = 0; await page.route('**/api/v1/documents/upload', r => { count++; return r.fulfill({ json: { document_id: 'multi' } }) })
    await openFiles(page, legacy); await page.locator('input[type=file]').evaluate((el: HTMLInputElement) => { el.multiple = true })
    await page.locator('input[type=file]').setInputFiles([file, { ...file, name: 'second.md' }]); await expect(page.getByRole('alert')).toContainText('请一次选择一份资料'); expect(count).toBe(0)
  })
}

test('cancel while classification is pending never opens a different source', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let classified = false; let aborted = false
  page.on('requestfailed', r => { if (r.url().includes('/organization')) aborted = true })
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'late', filename: file.name, status: 'ready' } }))
  await page.route('**/api/v1/documents/late/organization', async r => { classified = true; await gate; await r.fulfill({ json: {} }).catch(() => {}) })
  await openFiles(page); await page.locator('input[type=file]').setInputFiles(file); await expect.poll(() => classified).toBe(true)
  await page.getByRole('button', { name: '停止当前上传', exact: true }).click(); await expect.poll(() => aborted).toBe(true); release(); await expect(page).toHaveURL(/\/materials$/)
})
