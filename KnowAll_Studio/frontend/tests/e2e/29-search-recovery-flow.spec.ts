// Browser HTTP responses and session are explicit simulations; backend is tested separately.
import { test, expect } from './fixtures'

const detail = '检索服务暂不可用，请稍后重试'
const hit = { id: 'recovered', text: '故障恢复后的能量片段', metadata: { doc_id: 'temporary doc', page_range: '2' } }

for (const mode of ['plain', 'rag'] as const) {
  test(`${mode}: service error differs from empty and manual retry recovers`, async ({ page }, testInfo) => {
    let state: 'unavailable' | 'empty' | 'ready' = 'unavailable'
    const requests: string[] = []
    await page.route('**/api/v1/search/**', route => {
      requests.push(route.request().postDataJSON().query)
      if (state === 'unavailable') return route.fulfill({ status: 503, json: { detail } })
      if (mode === 'rag') return route.fulfill({ json: state === 'empty'
        ? { context: '', sources: [], has_results: false }
        : { context: hit.text, sources: [{ ...hit, source_id: 1 }], has_results: true } })
      return route.fulfill({ json: { results: state === 'empty' ? [] : [hit], count: state === 'empty' ? 0 : 1 } })
    })
    await page.goto('/search')
    if (mode === 'rag') await page.getByRole('switch').click()
    const input = page.locator('input.ant-input')
    const search = page.getByRole('button', { name: /^搜\s*索$/ })
    await input.fill('服务故障')
    await search.click()
    await expect(page.getByRole('alert').filter({ hasText: detail })).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('未找到相关内容', { exact: true })).toBeHidden()
    await page.screenshot({ path: testInfo.outputPath(`${mode}-error.png`), fullPage: true })
    state = 'empty'
    await input.fill('正常无结果')
    await search.click()
    await expect(page.getByText('未找到相关内容', { exact: true })).toBeVisible()
    await expect(page.getByText(detail, { exact: true })).toBeHidden()
    state = 'ready'
    await input.fill('恢复重试')
    await search.click()
    await expect(page.getByText('故障恢复后的能量片段', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('未找到相关内容', { exact: true })).toBeHidden()
    await expect(page.getByRole('link', { name: '查看原文' })).toHaveAttribute('href', '/materials/temporary%20doc')
    expect(requests).toContain('正常无结果')
    expect(requests).toContain('恢复重试')
    await page.screenshot({ path: testInfo.outputPath(`${mode}-recovered.png`), fullPage: true })
  })
}

test('changing mode discards late service failure and allows a new request', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  // 422 avoids Axios automatic retries so the single delayed response is controlled.
  await page.route('**/api/v1/search/', async route => {
    await gate
    await route.fulfill({ status: 422, json: { detail: '旧查询错误' } }).catch(() => {})
  })
  await page.route('**/api/v1/search/rag', route => route.fulfill({ json: {
    context: hit.text, sources: [{ ...hit, source_id: 1 }], has_results: true,
  } }))
  await page.goto('/search')
  await page.locator('input.ant-input').fill('旧请求')
  const started = page.waitForRequest('**/api/v1/search/')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await started
  const cancelled = page.waitForEvent('requestfailed', request => request.url().endsWith('/api/v1/search/'))
  await page.getByRole('switch').click()
  await cancelled
  release()
  await page.locator('input.ant-input').fill('新请求')
  await page.getByRole('button', { name: /^搜\s*索$/ }).click()
  await expect(page.getByText('检索上下文', { exact: true })).toBeVisible()
  await expect(page.getByText('旧查询错误', { exact: true })).toBeHidden()
})
