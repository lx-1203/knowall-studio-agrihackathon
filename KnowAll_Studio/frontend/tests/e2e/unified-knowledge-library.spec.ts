import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

test.setTimeout(60000)

async function discovery(page: Page) {
  const calls: URL[] = []
  await page.route('**/api/v1/knowledge-network/**', route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/documents')) return route.fulfill({ json: { items: [{ id: 'my-doc', filename: '我的高数笔记.md', status: 'ready' }] } })
    if (url.pathname.endsWith('/discover')) {
      calls.push(url)
      const matched = url.searchParams.get('document_id') === 'my-doc' || url.searchParams.get('q') === '导数'
      const items = matched ? [{ id: 'derivative', name: '导数', definition: '当前用户资料中的导数解释', subject: '数学', topic: '微分', aliases: [], review_status: 'needs_review', in_learning: false, source_count: 0 }] : []
      return route.fulfill({ json: { items, total: items.length } })
    }
    return route.fulfill({ json: {} })
  })
  return calls
}

test('one knowledge entry and an empty initial view without full-library requests', async ({ page }) => {
  const calls = await discovery(page)
  await page.goto('/knowledge-network', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '知识库', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '知识库', exact: true })).toHaveCount(1)
  await expect(page.getByRole('link', { name: '知识点集锦', exact: true })).toHaveCount(0)
  await expect(page.getByText('选择自己的资料或输入关键词，查看匹配的知识点集锦', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '检索', exact: true })).toBeDisabled()
  expect(calls).toHaveLength(0)
})

test('selected owned document scopes discovery and public mode resets its scope', async ({ page }) => {
  const calls = await discovery(page)
  await page.goto('/knowledge-network', { waitUntil: 'domcontentloaded' })
  await page.getByRole('combobox', { name: '选择上传资料', exact: true }).click()
  await page.getByText('我的高数笔记.md', { exact: true }).last().click()
  await expect(page.getByRole('button', { name: '学习 导数', exact: true })).toBeVisible()
  expect(calls.at(-1)?.searchParams.get('document_id')).toBe('my-doc')
  await page.getByRole('button', { name: '公共知识检索', exact: true }).click()
  await expect(page.getByRole('button', { name: '学习 导数', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '检索', exact: true })).toBeDisabled()
  expect(calls).toHaveLength(1)
})

test('keyword results appear on submit and disappear when the keyword is cleared', async ({ page }) => {
  const calls = await discovery(page)
  await page.goto('/knowledge-network', { waitUntil: 'domcontentloaded' })
  await page.getByRole('textbox', { name: '搜索知识库', exact: true }).fill('导数')
  expect(calls).toHaveLength(0)
  await page.getByRole('button', { name: '检索', exact: true }).click()
  await expect(page.getByRole('button', { name: '学习 导数', exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '搜索知识库', exact: true }).fill('')
  await expect(page.getByRole('button', { name: '学习 导数', exact: true })).toHaveCount(0)
  expect(calls).toHaveLength(1)
})

test('old collections URLs redirect to the unified library and preserve query intent', async ({ page }) => {
  const calls = await discovery(page)
  await page.goto('/knowledge-collections?scope=public&q=导数', { waitUntil: 'domcontentloaded' })
  await expect(page).toHaveURL(/knowledge-network\?scope=public&q=/)
  await expect(page.getByRole('heading', { name: '知识库', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '学习 导数', exact: true })).toBeVisible()
  expect(calls.at(-1)?.searchParams.get('scope')).toBe('public')
})

test('completed uploads link directly to knowledge matched to that file', async ({ page }) => {
  await page.route('**/api/v1/documents/upload', route => route.fulfill({ json: {
    document_id: 'my-doc', filename: 'notes.md', total_chunks: 1,
    knowledge_extraction: { id: 'my-job', document_id: 'my-doc', status: 'completed', point_count: 2, error: null },
  } }))
  await page.goto('/upload', { waitUntil: 'domcontentloaded' })
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('Test knowledge source') })
  await expect(page.getByRole('link', { name: '查看这份资料的知识', exact: true })).toHaveAttribute('href', '/knowledge-network?scope=personal&document=my-doc')
})
