import { test, expect, E2E_TOKEN } from './fixtures'
import { docxFixture } from './docx-fixture'

const doc = { id: 'docx-fixture', filename: 'biology.docx', file_type: 'docx', status: 'ready', total_chunks: 1 }
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/documents/docx-fixture', r => r.fulfill({ json: doc }))
  await page.route('**/api/v1/documents/docx-fixture/chunks', r => r.fulfill({ json: { chunks: [{ id: 'section', index: 0, text: '正文备用', page_range: 'p1' }] } }))
})

test('real protected DOCX retains table/image/formatting and removes executable link before DOM commit', async ({ page }) => {
  const bytes = await docxFixture()
  const headers: string[] = []
  // Inspect the first sink commit too, so a later cleanup cannot hide unsafe HTML.
  await page.addInitScript(() => {
    const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML')!
    Object.defineProperty(Element.prototype, 'innerHTML', { ...desc, set(value) { desc.set!.call(this, value); if (this.classList.contains('docx-preview') && this.querySelector('a[href^="javascript:"]')) (window as any).unsafeDocxCommitted = true } })
  })
  await page.route('**/api/v1/documents/docx-fixture/raw', r => { headers.push(r.request().headers().authorization); return r.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', body: bytes }) })
  await page.goto('/materials/docx-fixture')
  await expect(page.locator('.docx-preview')).toContainText('文档预览正文')
  await expect(page.locator('.docx-preview strong')).toHaveText('文档预览正文')
  await expect(page.locator('.docx-preview table')).toContainText('表格能量')
  await expect(page.locator('.docx-preview table')).toContainText('ATP')
  await expect(page.locator('.docx-preview img')).toHaveAttribute('src', /^data:image\/png;base64,/)
  await expect(page.locator('.docx-preview img')).toHaveJSProperty('complete', true)
  expect(await page.locator('.docx-preview img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1)
  await expect(page.locator('.docx-preview a').filter({ hasText: '正常链接' })).toHaveAttribute('href', 'https://example.com/lesson')
  await expect(page.locator('.docx-preview a[href^="javascript:"]')).toHaveCount(0)
  expect(await page.evaluate(() => (window as any).unsafeDocxCommitted)).toBeUndefined()
  expect(headers).toEqual([`Bearer ${E2E_TOKEN}`])
  expect(page.url()).not.toContain(E2E_TOKEN)
  await page.screenshot({ path: `../docs/open-source-integration/${process.env.OSS_EVIDENCE_PREFIX || 'round-03'}-docx-preview.png` })
})

for (const status of [401, 204]) {
  test(`protected file ${status} shows retry and retry opens DOCX`, async ({ page }) => {
    let calls = 0
    const bytes = await docxFixture('重试正文')
    await page.route('**/api/v1/documents/docx-fixture/raw', r => {
      expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
      calls++
      return calls === 1 ? r.fulfill({ status }) : r.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', body: bytes })
    })
    await page.goto('/materials/docx-fixture')
    await expect(page.getByText('原文件未能加载，请重试或切换到阅读正文。')).toBeVisible()
    await expect(page.locator('.docx-preview')).toHaveCount(0)
    await page.getByRole('button', { name: '重新加载原文件' }).click()
    await expect(page.locator('.docx-preview')).toContainText('重试正文')
    expect(calls).toBe(2)
  })
}

test('invalid OOXML stays an error and reading text remains available', async ({ page }) => {
  await page.route('**/api/v1/documents/docx-fixture/raw', r => r.fulfill({ body: 'not-a-zip-file' }))
  await page.goto('/materials/docx-fixture')
  await expect(page.getByText(/加载失败:/)).toBeVisible()
  await expect(page.locator('.docx-preview')).toHaveCount(0)
  await page.getByText('阅读正文', { exact: true }).click()
  await expect(page.locator('.material-reading-text')).toContainText('正文备用')
})

test('URL switch aborts old component fetch instead of displaying its late result', async ({ page }) => {
  const first = (await docxFixture('旧正文')).toString('base64')
  const second = await docxFixture('当前正文')
  await page.route('**/docx-new', r => r.fulfill({ body: second }))
  await page.goto('/dashboard')
  await page.evaluate(async first => {
    const original = window.fetch.bind(window)
    window.fetch = (input, init) => {
      if (String(input).endsWith('/docx-old')) {
        ;(window as any).oldFetchStarted = true
        init?.signal?.addEventListener('abort', () => { (window as any).oldFetchAborted = true })
        return new Promise(resolve => { (window as any).releaseOldFetch = () => resolve(new Response(Uint8Array.from(atob(first), c => c.charCodeAt(0)))) })
      }
      return original(input, init)
    }
    const harness = await import('/.tmp/docxHarness.tsx')
    harness.show('/docx-old')
  }, first)
  await expect.poll(() => page.evaluate(() => (window as any).oldFetchStarted)).toBe(true)
  await page.evaluate(async () => (await import('/.tmp/docxHarness.tsx')).show('/docx-new'))
  await expect(page.locator('.docx-preview')).toContainText('当前正文')
  await expect.poll(() => page.evaluate(() => (window as any).oldFetchAborted)).toBe(true)
  await page.evaluate(() => (window as any).releaseOldFetch())
  await expect(page.locator('.docx-preview')).toHaveText(/当前正文/)
  await expect(page.locator('.docx-preview')).toHaveCSS('max-height', '321px')
})

test('late Mammoth conversion cannot replace the newly selected document', async ({ page }) => {
  await page.route('**/docx-old', async r => r.fulfill({ body: await docxFixture('转换旧正文') }))
  await page.route('**/docx-new', async r => r.fulfill({ body: await docxFixture('转换当前正文') }))
  await page.goto('/dashboard')
  await page.evaluate(async () => { const h = await import('/.tmp/docxHarness.tsx'); h.delayFirstConversion(); h.show('/docx-old') })
  await expect.poll(() => page.evaluate(() => (window as any).oldDocxReady)).toBe(true)
  await page.evaluate(async () => (await import('/.tmp/docxHarness.tsx')).show('/docx-new'))
  await expect(page.locator('.docx-preview')).toContainText('转换当前正文')
  await page.evaluate(() => (window as any).releaseOldDocx())
  await expect.poll(() => page.locator('.docx-preview').textContent()).toContain('转换当前正文')
  await expect(page.locator('.docx-preview')).not.toContainText('转换旧正文')
})
