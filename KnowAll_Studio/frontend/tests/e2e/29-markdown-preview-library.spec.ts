import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const lesson = '# 数学与代码\n\n行内 $x^2 + y^2$，价格 \\$5 和 \\$10。\n\n$$\n\\frac{1}{2} + \\sqrt{4}\n$$\n\n```python\ndef study(value):\n    return value + 1\n```\n\n`$source$`\n\n|学科|进度|\n|---|---|\n|数学|50%|\n\n- [x] 已学习\n\n[资料](https://example.com/lesson)\n'
async function file(page: Page, body = lesson, type = 'md', name = 'lesson.md') {
  const headers: string[] = []
  await page.route('**/api/v1/documents/markdown-fixture', r => r.fulfill({ json: { id: 'markdown-fixture', filename: name, file_type: type, status: 'ready', total_chunks: 1 } }))
  await page.route('**/api/v1/documents/markdown-fixture/chunks', r => r.fulfill({ json: { chunks: [{ id: 'markdown-section', index: 0, text: '数学资料', page_range: 'p1' }] } }))
  await page.route('**/api/v1/documents/markdown-fixture/raw', r => { headers.push(r.request().headers().authorization); return r.fulfill({ body }) })
  return headers
}
async function harness(page: Page, body: string, name = 'lesson.md', type?: string) {
  await page.route('**/markdown-current', r => r.fulfill({ body }))
  await page.goto('/dashboard')
  await page.evaluate(async ({ name, type }) => (await import('/.tmp/markdownHarness.tsx')).show('/markdown-current', name, type), { name, type })
}

test('protected original renders inline/block math with GFM and escaped currency', async ({ page }) => {
  const headers = await file(page)
  await page.goto('/materials/markdown-fixture')
  const reader = page.locator('.markdown-preview')
  await expect(reader.locator('.katex')).toHaveCount(2)
  await expect(reader.locator('.katex-display')).toHaveCount(1)
  await expect(reader.locator('annotation').first()).toHaveText('x^2 + y^2')
  await expect(reader).toContainText('价格 $5 和 $10')
  await expect(reader.locator('table td')).toHaveText(['数学', '50%'])
  await expect(reader.locator('input[type=checkbox]')).toBeChecked()
  await expect(reader.getByRole('link', { name: '资料' })).toHaveAttribute('href', 'https://example.com/lesson')
  expect(headers.length).toBeGreaterThan(0)
  expect(headers.every(h => h === `Bearer ${E2E_TOKEN}`)).toBe(true)
})

test('native lazy syntax color preserves fences and inline code', async ({ page }) => {
  const requests: string[] = []
  page.on('request', r => requests.push(r.url()))
  await file(page)
  await page.goto('/materials/markdown-fixture')
  const reader = page.locator('.markdown-preview')
  await expect(reader.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
  await expect(reader.locator('pre code').last()).toHaveText('def study(value):\n    return value + 1\n')
  await expect(reader.locator('p > code')).toHaveText('$source$')
  const evidence = path.resolve('../docs/open-source-integration')
  // Vite may prebundle native imports into anonymous chunks. The packaged
  // smoke verifies named production grammar requests; this records dev traffic.
  const prefix = process.env.OSS_EVIDENCE_PREFIX || 'round-06'
  fs.writeFileSync(path.join(evidence, `${prefix}-markdown-browser-requests.json`), JSON.stringify(requests, null, 2))
  await reader.screenshot({ path: path.join(evidence, `${prefix}-markdown-preview.png`) })
})

test('metadata type selects Markdown; explicit txt preserves raw source', async ({ page }) => {
  await file(page, '# 元数据标题\n\n$x^2$', 'md', '无后缀资料')
  await page.goto('/materials/markdown-fixture')
  await expect(page.locator('.markdown-preview h1')).toHaveText('元数据标题')
  const source = '# 原文\n\n$x$\n<script>alert(1)</script>\n'
  await harness(page, source, 'confusing.md', 'txt')
  await expect(page.locator('#markdown-test-mount pre')).toHaveText(source)
  await expect(page.locator('#markdown-test-mount h1, #markdown-test-mount .katex')).toHaveCount(0)
})

test('common js/py/ts fence aliases resolve to native grammars', async ({ page }) => {
  await file(page, '```js\nconst study = true;\n```\n\n```py\ndef learn():\n    return 1\n```\n\n```ts\ninterface Lesson { title: string }\n```\n')
  await page.goto('/materials/markdown-fixture')
  const blocks = page.locator('.markdown-preview pre')
  await expect(blocks.nth(0).locator('span').filter({ hasText: /^const$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
  await expect(blocks.nth(1).locator('span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
  await expect(blocks.nth(2).locator('span').filter({ hasText: /^interface$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
})

test('untrusted HTML links and KaTeX commands stay inert; bad math remains readable', async ({ page }) => {
  const source = '# 安全\n\n<img src=x onerror="window.markdownExecuted=1">\n<script>window.markdownExecuted=2</script>\n\n[危险](javascript:alert(1))\n\n$\\href{javascript:alert(1)}{bad}$\n\n$\\htmlClass{owned}{bad}$\n\n$\\frac{$\n\n```math\nx^2\n```\n\n```unknown-language\n<img onerror=alert(1)>\n```\n'
  await file(page, source)
  await page.goto('/materials/markdown-fixture')
  const reader = page.locator('.markdown-preview')
  await expect(reader.locator('.katex-error')).toContainText('\\frac{')
  await expect(reader.locator('.katex-display .katex')).toHaveCount(1)
  await expect(reader.locator('img, script, .owned, a[href^="javascript:"]')).toHaveCount(0)
  await expect(reader.locator('pre code').last()).toHaveText('<img onerror=alert(1)>\n')
  expect(await page.evaluate(() => (window as unknown as { markdownExecuted?: number }).markdownExecuted)).toBeUndefined()
})

test('switch aborts previous read and ignores late success while respecting height', async ({ page }) => {
  await page.route('**/markdown-current', r => r.fulfill({ body: '# 当前资料' }))
  await page.goto('/dashboard')
  await page.evaluate(async () => {
    const original = window.fetch.bind(window)
    const state = window as unknown as { oldMarkdownStarted: boolean; oldMarkdownAborted: boolean; releaseMarkdown: () => void }
    window.fetch = (input, init) => {
      if (String(input).endsWith('/markdown-old')) {
        state.oldMarkdownStarted = true
        init?.signal?.addEventListener('abort', () => { state.oldMarkdownAborted = true })
        return new Promise(resolve => { state.releaseMarkdown = () => resolve(new Response('# 迟到旧资料')) })
      }
      return original(input, init)
    }
    ;(await import('/.tmp/markdownHarness.tsx')).show('/markdown-old')
  })
  await expect.poll(() => page.evaluate(() => (window as unknown as { oldMarkdownStarted?: boolean }).oldMarkdownStarted)).toBe(true)
  await page.evaluate(async () => (await import('/.tmp/markdownHarness.tsx')).show('/markdown-current'))
  await expect(page.locator('#markdown-test-mount h1')).toHaveText('当前资料')
  await expect.poll(() => page.evaluate(() => (window as unknown as { oldMarkdownAborted?: boolean }).oldMarkdownAborted)).toBe(true)
  await page.evaluate(async () => { (window as unknown as { releaseMarkdown: () => void }).releaseMarkdown(); await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))) })
  await expect(page.locator('#markdown-test-mount')).not.toContainText('迟到旧资料')
  await expect(page.locator('#markdown-test-mount .markdown-preview')).toHaveCSS('max-height', '321px')
})

test('long TXT displays original prefix and an honest independent limit notice', async ({ page }) => {
  const source = '中文'.repeat(50010)
  await harness(page, source, 'lesson.txt', 'txt')
  await expect.poll(async () => (await page.locator('#markdown-test-mount pre').textContent())?.length).toBe(100000)
  expect(await page.locator('#markdown-test-mount pre').textContent()).toBe(source.slice(0, 100000))
  await expect(page.locator('#markdown-test-mount .markdown-limit')).toContainText('当前仅显示部分内容')
  await expect(page.locator('#markdown-test-mount')).not.toContainText('100KB')
  await expect(page.locator('#markdown-test-mount pre')).not.toContainText('内容过长')
})

test('dark token styling and native code theme apply in the reader', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('knowall-theme', 'dark'))
  await harness(page, lesson)
  const reader = page.locator('#markdown-test-mount .markdown-preview')
  await expect(reader).toHaveCSS('background-color', 'rgb(20, 38, 54)')
  await expect(reader).toHaveCSS('color', 'rgb(228, 237, 244)')
  await expect(reader.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(86, 156, 214)')
})

test('authorized raw 401 retries and missing original gives readable error', async ({ page }) => {
  await file(page)
  let reads = 0
  await page.route('**/api/v1/documents/markdown-fixture/raw', r => {
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    return ++reads === 1 ? r.fulfill({ status: 401 }) : r.fulfill({ body: lesson })
  })
  await page.goto('/materials/markdown-fixture')
  await page.getByRole('button', { name: '重新加载原文件' }).click()
  await expect(page.locator('.markdown-preview .katex')).toHaveCount(2)
  expect(reads).toBe(2)
  await page.route('**/markdown-current', r => r.fulfill({ status: 204 }))
  await page.goto('/dashboard')
  await page.evaluate(async () => (await import('/.tmp/markdownHarness.tsx')).show('/markdown-current'))
  await expect(page.locator('#markdown-test-mount')).toContainText('原始文件暂不可用')
})
