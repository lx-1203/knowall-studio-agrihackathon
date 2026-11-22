import { test, expect, E2E_TOKEN } from './fixtures'

const hit = (text = '线粒体参与能量转换') => ({ id: 'chunk', text, metadata: { doc_id: 'doc id', page_range: '3-4' } })

test('retrieval context displays the same fragment and document source', async ({ page }) => {
  await page.route('**/api/v1/search/rag', route => route.fulfill({ json: { context: '线粒体参与能量转换', has_results: true, sources: [{ ...hit(), source_id: 1 }] } }))
  await page.goto('/search')
  await page.getByRole('switch').click()
  await page.locator('input.ant-input').fill('线粒体')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('检索上下文', { exact: true })).toBeVisible()
  await expect(page.getByText(/来源1/)).toBeVisible()
  await expect(page.getByRole('link', { name: '查看原文' })).toHaveAttribute('href', '/materials/doc%20id')
  await expect(page.getByText('3-4')).toBeVisible()
})

test('plain search opens the authenticated material page instead of raw API', async ({ page }) => {
  await page.route('**/api/v1/search/', route => route.fulfill({ json: { results: [hit()] } }))
  await page.goto('/search')
  await page.locator('input.ant-input').fill('线粒体')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByRole('link', { name: '查看原文' })).toHaveAttribute('href', '/materials/doc%20id')
})

test('empty context is visible after retrieval completes', async ({ page }) => {
  await page.route('**/api/v1/search/rag', route => route.fulfill({ json: { context: '', has_results: false, sources: [] } }))
  await page.goto('/search')
  await page.getByRole('switch').click()
  await page.locator('input.ant-input').fill('无结果')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('未找到相关内容')).toBeVisible()
})

test('switching mode discards a late response from the previous mode', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/search/', async route => { await gate; await route.fulfill({ json: { results: [hit('迟到旧结果')] } }).catch(() => {}) })
  await page.goto('/search')
  await page.locator('input.ant-input').fill('旧关键词')
  const started = page.waitForRequest('**/api/v1/search/')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await started
  const cancelled = page.waitForEvent('requestfailed', request => request.url().endsWith('/api/v1/search/'))
  await page.getByRole('switch').click()
  await cancelled
  release()
  await page.getByRole('switch').click()
  await expect(page.getByText('迟到旧结果')).toBeHidden()
})

test('failed search clears the previous result and shows the actual error', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/search/', route => ++calls === 1
    ? route.fulfill({ json: { results: [hit('旧结果')] } })
    : route.fulfill({ status: 503, json: { detail: '检索服务暂不可用' } }))
  await page.goto('/search')
  await page.locator('input.ant-input').fill('第一轮')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('旧结果')).toBeVisible()
  await page.locator('input.ant-input').fill('第二轮')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('旧结果')).toBeHidden()
  await expect(page.getByText('检索服务暂不可用')).toBeVisible()
})

test('local research reference links to the material and shows its assigned number', async ({ page }) => {
  const task = { task_id: 'local', topic: '本地研究', status: 'completed', progress: 1, sub_topics: [], report: '本地发现[来源1]',
    sources: [{ source_id: 1, title: '资料分片', source: 'knowledge_base', url: '', doc_id: 'doc id', chunk_id: 'chunk', page_range: '3-4' }] }
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [task] } }))
  await page.route('**/api/v1/research/local/status', route => route.fulfill({ json: task }))
  await page.goto('/research')
  await page.getByRole('button', { name: '查看', exact: true }).click()
  await expect(page.getByRole('link', { name: /资料分片/ })).toHaveAttribute('href', '/materials/doc%20id')
  await expect(page.getByText('3-4')).toBeVisible()
  await expect(page.getByText('来源1', { exact: true })).toBeVisible()
})

test('validation error arrays produce a readable message without crashing', async ({ page }) => {
  await page.route('**/api/v1/search/', route => route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'query'], msg: 'too long', type: 'string_too_long' }] } }))
  await page.goto('/search')
  await page.locator('input.ant-input').fill('参数错误')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('搜索失败，请重试')).toBeVisible()
  await expect(page.getByText('统一搜索', { exact: true })).toBeVisible()
})

test('following the source fetches the original with the session authorization', async ({ page }) => {
  await page.route('**/api/v1/search/', route => route.fulfill({ json: { results: [hit()] } }))
  const headers: string[] = []
  await page.route('**/api/v1/documents/**', route => {
    headers.push(route.request().headers()['authorization'] || '')
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/raw')) return route.fulfill({ contentType: 'text/plain', body: '受控资料原文：能量转换' })
    if (url.pathname.endsWith('/chunks')) return route.fulfill({ json: { chunks: [] } })
    return route.fulfill({ json: { id: 'doc id', filename: 'biology.txt', file_type: 'txt', status: 'ready' } })
  })
  await page.goto('/search')
  await page.locator('input.ant-input').fill('能量')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await page.getByRole('link', { name: '查看原文' }).click()
  await expect(page.getByText('受控资料原文：能量转换')).toBeVisible()
  expect(headers.length).toBeGreaterThanOrEqual(3)
  expect(headers.every(header => header === `Bearer ${E2E_TOKEN}`)).toBe(true)
  expect(page.url()).not.toContain(E2E_TOKEN)
})

