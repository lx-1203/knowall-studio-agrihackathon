// HTTP responses/session are simulations; SQL and permissions have separate real tests.
import { test, expect } from './fixtures'

const input = (page: import('@playwright/test').Page) => page.getByRole('textbox', { name: '搜索关键词或问题' })
const hit = (id: string, text: string, filename = '计算机网络.pdf') => ({
  id, text, metadata: { doc_id: 'network', filename, page_range: '3-4' },
})

test('default keyword search groups sources and shows matching excerpts', async ({ page }, info) => {
  const requests: any[] = []
  const text = '开头内容。'.repeat(160) + 'TCP 三次握手建立连接。' + '结束内容。'.repeat(100)
  await page.route('**/api/v1/search/', route => {
    requests.push(route.request().postDataJSON())
    return route.fulfill({ json: { results: [hit('a', text), hit('b', 'TCP 的另一个三次握手片段')] } })
  })
  await page.goto('/search')
  await expect(input(page)).toBeVisible()
  await page.screenshot({ path: info.outputPath('search-initial.png'), fullPage: true })
  await input(page).fill('TCP 三次握手')
  await input(page).press('Enter')
  await expect(page.getByText('计算机网络.pdf', { exact: true })).toHaveCount(1)
  expect(requests[0]).toMatchObject({ query: 'TCP 三次握手', mode: 'keyword' })
  await expect(page.locator('.search-excerpt').first()).toContainText('TCP 三次握手')
  expect((await page.locator('.search-excerpt').first().innerText()).length).toBeLessThan(350)
  await expect(page.locator('mark').filter({ hasText: 'TCP' }).first()).toBeVisible()
  await expect(page.locator('mark').filter({ hasText: '三次握手' }).first()).toBeVisible()
  await page.getByRole('button', { name: '展开片段' }).click()
  await expect(page.locator('.search-excerpt').first()).toContainText('开头内容。'.repeat(80))
  await page.getByRole('button', { name: '收起片段' }).click()
  await page.screenshot({ path: info.outputPath('search-results-desktop.png'), fullPage: true })
})

test('Chinese composition Enter does not submit a partial search', async ({ page }) => {
  const requests: any[] = []
  await page.route('**/api/v1/search/', route => {
    requests.push(route.request().postDataJSON())
    return route.fulfill({ json: { results: [] } })
  })
  await page.goto('/search')
  await input(page).fill('线粒体')
  await input(page).dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await expect(page.getByText('未找到相关内容', { exact: true })).toBeHidden()
  await input(page).press('Enter')
  await expect(page.getByText('未找到相关内容', { exact: true })).toBeVisible()
  expect(requests).toHaveLength(1)
})

test('a new search can replace a slow request and its late result stays hidden', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/search/', async route => {
    if (route.request().postDataJSON().query === '旧词') {
      await gate
      await route.fulfill({ json: { results: [hit('old', '迟到结果')] } }).catch(() => {})
    } else await route.fulfill({ json: { results: [hit('new', '新词的结果')] } })
  })
  await page.goto('/search')
  await input(page).fill('旧词')
  const started = page.waitForRequest('**/api/v1/search/')
  await input(page).press('Enter')
  await started
  await expect(page.getByRole('button', { name: '取消搜索' })).toBeVisible()
  await input(page).fill('新词')
  await input(page).press('Enter')
  await expect(page.getByText('新词的结果', { exact: true })).toBeVisible()
  release()
  await expect(page.getByText('迟到结果', { exact: true })).toBeHidden()
})

test('cancel stops waiting and lets the same query retry', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let calls = 0
  await page.route('**/api/v1/search/', async route => {
    if (++calls === 1) await gate
    await route.fulfill({ json: { results: [hit('a', '恢复结果')] } }).catch(() => {})
  })
  await page.goto('/search')
  await input(page).fill('取消')
  await input(page).press('Enter')
  await page.getByRole('button', { name: '取消搜索' }).click()
  await expect(page.getByText('已取消搜索')).toBeVisible()
  release()
  await input(page).press('Enter')
  await expect(page.getByText('恢复结果', { exact: true })).toBeVisible()
})

test('query survives visiting a material and browser back', async ({ page }) => {
  const requests: string[] = []
  await page.route('**/api/v1/search/', route => {
    requests.push(route.request().postDataJSON().query)
    return route.fulfill({ json: { results: [hit('a', 'TCP 恢复片段')] } })
  })
  await page.route('**/api/v1/documents/network**', route => route.fulfill({ json: { id: 'network', filename: '计算机网络.pdf', file_type: 'txt', status: 'ready', chunks: [] } }))
  await page.goto('/search')
  await input(page).fill('TCP')
  await input(page).press('Enter')
  await expect(page.getByText('TCP 恢复片段', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/q=TCP/)
  await page.getByRole('link', { name: '查看原文' }).click()
  await expect(page).toHaveURL(/materials\/network/)
  await page.goBack()
  await expect(input(page)).toHaveValue('TCP')
  await expect(page.getByText('TCP 恢复片段', { exact: true })).toBeVisible()
  expect(requests.length).toBeGreaterThanOrEqual(2)
})

test('direct query URL restores search and clear removes old result', async ({ page }) => {
  await page.route('**/api/v1/search/', route => route.fulfill({ json: { results: [hit('a', '能量转换')] } }))
  await page.goto('/search?q=能量&mode=keyword')
  await expect(input(page)).toHaveValue('能量')
  await expect(page.getByText('能量转换', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '清空搜索' }).click()
  await expect(input(page)).toHaveValue('')
  await expect(page.getByText('能量转换', { exact: true })).toBeHidden()
  await expect(page).toHaveURL(/\/search$/)
})

test('slow keyword search times out visibly and manual retry recovers', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let calls = 0
  await page.route('**/api/v1/search/', async route => {
    if (++calls === 1) await gate
    await route.fulfill({ json: { results: [hit('a', '超时后恢复')] } }).catch(() => {})
  })
  await page.goto('/search')
  await input(page).fill('等待超时')
  await input(page).press('Enter')
  await expect(page.getByRole('alert')).toContainText('搜索等待超时', { timeout: 19000 })
  await expect(page.getByText('未找到相关内容', { exact: true })).toBeHidden()
  release()
  await page.getByRole('button', { name: '重试搜索' }).click()
  await expect(page.getByText('超时后恢复', { exact: true })).toBeVisible()
})

test('mobile results and dark mode fit viewport', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/v1/search/', route => route.fulfill({ json: { results: [hit('a', 'TCP 三次握手：'.repeat(160), '一个很长的资料文件名称'.repeat(12) + '.pdf')] } }))
  await page.goto('/search')
  await input(page).fill('TCP')
  await input(page).press('Enter')
  await expect(page.getByRole('link', { name: '查看原文' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: info.outputPath('search-mobile.png'), fullPage: true })
  await page.getByRole('button', { name: /切换到深色|深色模式/ }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('.product-content')).toHaveCSS('background-color', 'rgb(14, 29, 42)')
  await page.screenshot({ path: info.outputPath('search-dark-mobile.png'), fullPage: true, animations: 'disabled' })
})
