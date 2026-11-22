import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

async function sourceText(page: Page) {
  return page.locator('pre code').last().evaluate(node => {
    // Line numbers are UI, not part of the original source file.
    const copy = node.cloneNode(true) as HTMLElement
    copy.querySelectorAll('.react-syntax-highlighter-line-number').forEach(line => line.remove())
    return copy.textContent
  })
}

async function codeDocument(page: Page, type: string, source: string) {
  const headers: string[] = []
  await page.route('**/api/v1/documents/code-fixture', r => r.fulfill({ json: { id: 'code-fixture', filename: `lesson.${type}`, file_type: type, status: 'ready', total_chunks: 1 } }))
  await page.route('**/api/v1/documents/code-fixture/chunks', r => r.fulfill({ json: { chunks: [{ id: 'section', index: 0, text: source, page_range: 'p1' }] } }))
  await page.route('**/api/v1/documents/code-fixture/raw', r => { headers.push(r.request().headers().authorization); return r.fulfill({ contentType: 'text/plain; charset=utf-8', body: source }) })
  return headers
}

for (const item of [
  { type: 'py', source: 'def greet(name):\n    message = "你好"\n    print(message)\n', keyword: 'def' },
  { type: 'tsx', source: 'interface Props { name: string }\nconst Card = ({ name }: Props) => <strong>{name}</strong>;\n', keyword: 'interface' },
  { type: 'json', source: '{ "course": "生物学", "ready": true }', keyword: 'true' },
]) {
  test(`protected ${item.type} file has real syntax colors, line numbers and original Unicode`, async ({ page }) => {
    const requested: string[] = []
    page.on('request', r => requested.push(r.url()))
    const headers = await codeDocument(page, item.type, item.source)
    await page.goto('/materials/code-fixture')
    await expect.poll(() => sourceText(page)).toBe(item.source)
    const token = page.locator('pre code .token').filter({ hasText: new RegExp(`^${item.keyword}$`) }).first()
    await expect(token).toBeVisible()
    const colors = await token.evaluate(node => ({ token: getComputedStyle(node).color, base: getComputedStyle(node.closest('code')!).color }))
    expect(colors.token).not.toBe(colors.base)
    await expect(page.locator('.react-syntax-highlighter-line-number').first()).toHaveText('1')
    expect(headers.length).toBeGreaterThan(0)
    expect(headers.every(header => header === `Bearer ${E2E_TOKEN}`)).toBe(true)
    if (item.type === 'py') {
      const evidencePrefix = process.env.OSS_EVIDENCE_PREFIX || 'round-04'
      await page.screenshot({ path: `../docs/open-source-integration/${evidencePrefix}-code-preview.png` })
      await writeFile(`../docs/open-source-integration/${evidencePrefix}-browser-language-requests.json`, JSON.stringify(requested, null, 2))
    }
  })
}

test('HTML source stays visible code and cannot create script/image DOM', async ({ page }) => {
  const source = '<script>window.codeExecuted = true</script>\n<img src=x onerror="window.codeExecuted=true">'
  await codeDocument(page, 'html_code', source)
  await page.goto('/materials/code-fixture')
  await expect.poll(() => sourceText(page)).toBe(source)
  await expect(page.locator('pre code .token').first()).toBeVisible()
  await expect(page.locator('pre code script, pre code img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as any).codeExecuted)).toBeUndefined()
})

test('unknown file remains plain text and respects caller height', async ({ page }) => {
  await page.route('**/code-unknown', r => r.fulfill({ body: '自定义格式 <value>原文</value>' }))
  await page.goto('/dashboard')
  await page.evaluate(async () => (await import('/.tmp/codeHarness.tsx')).show('/code-unknown', 'custom'))
  await expect(page.locator('#code-test-mount pre code').last()).toContainText('自定义格式 <value>原文</value>')
  await expect(page.locator('#code-test-mount > div')).toHaveCSS('max-height', '321px')
  await expect(page.locator('#code-test-mount pre code .token')).toHaveCount(0)
})

test('URL switch aborts old read and ignores its late success', async ({ page }) => {
  await page.route('**/code-current', r => r.fulfill({ body: '当前资料\n第二行' }))
  await page.goto('/dashboard')
  await page.evaluate(async () => {
    const original = window.fetch.bind(window)
    window.fetch = (input, init) => {
      if (String(input).endsWith('/code-old')) {
        ;(window as any).oldCodeStarted = true
        init?.signal?.addEventListener('abort', () => { (window as any).oldCodeAborted = true })
        // Ignore abort intentionally: stale completion still must be guarded.
        return new Promise(resolve => { (window as any).releaseOldCode = () => resolve(new Response('迟到旧资料')) })
      }
      return original(input, init)
    }
    ;(await import('/.tmp/codeHarness.tsx')).show('/code-old', 'custom')
  })
  await expect.poll(() => page.evaluate(() => (window as any).oldCodeStarted)).toBe(true)
  await page.evaluate(async () => (await import('/.tmp/codeHarness.tsx')).show('/code-current', 'custom'))
  await expect(page.locator('#code-test-mount pre code').last()).toContainText('当前资料')
  await expect.poll(() => page.evaluate(() => (window as any).oldCodeAborted)).toBe(true)
  await page.evaluate(async () => { (window as any).releaseOldCode(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
  await expect(page.locator('#code-test-mount pre code').last()).not.toContainText('迟到旧资料')
})

test('long source uses an honest partial-content notice and cuts the old display limit', async ({ page }) => {
  await page.route('**/code-long', r => r.fulfill({ body: '界'.repeat(200000) + '不应显示的尾部' }))
  await page.goto('/dashboard')
  await page.evaluate(async () => (await import('/.tmp/codeHarness.tsx')).show('/code-long', 'custom'))
  await expect(page.locator('#code-test-mount pre code').last()).toContainText('文件较长，仅显示部分内容。')
  await expect(page.locator('#code-test-mount pre code').last()).not.toContainText('不应显示的尾部')
  await expect(page.locator('#code-test-mount pre code').last()).not.toContainText('200KB')
})

test('protected 401 can be retried without bypassing current account', async ({ page }) => {
  await codeDocument(page, 'py', 'def retry():\n    return True')
  let count = 0
  await page.route('**/api/v1/documents/code-fixture/raw', r => {
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    count++
    return count === 1 ? r.fulfill({ status: 401 }) : r.fulfill({ body: 'def retry():\n    return True' })
  })
  await page.goto('/materials/code-fixture')
  await expect(page.getByText('原文件未能加载，请重试或切换到阅读正文。')).toBeVisible()
  await page.getByRole('button', { name: '重新加载原文件' }).click()
  await expect(page.locator('pre code').last()).toContainText('def retry():')
  expect(count).toBe(2)
})

test('grammar failure keeps authorized Python readable as plain code', async ({ page }) => {
  const source = 'def readable():\n    return "语法模块失败，原文仍在"'
  const headers = await codeDocument(page, 'py', source)
  let blocked = 0
  await page.route(/\/python-[^/]+\.js(?:\?.*)?$/, r => { blocked++; return r.abort('failed') })
  await page.goto('/materials/code-fixture')
  await expect.poll(() => blocked).toBeGreaterThan(0)
  await expect.poll(() => sourceText(page)).toBe(source)
  await expect(page.locator('pre code .token')).toHaveCount(0)
  expect(headers.every(header => header === `Bearer ${E2E_TOKEN}`)).toBe(true)
})

