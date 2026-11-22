import { test, expect, E2E_TOKEN } from './fixtures'
import type { Page } from '@playwright/test'
import * as XLSX from 'xlsx'

function workbook(type: 'xlsx' | 'xls' = 'xlsx') {
  const wb = XLSX.utils.book_new()
  const sheet = XLSX.utils.aoa_to_sheet([
    ['学科', '学科', '', 123, '_key', '__proto__'],
    ['生物', '化学', '空头值', '数字头', '原键值', '<img src=x onerror="window.sheetExecuted=true">'],
  ])
  XLSX.utils.book_append_sheet(wb, sheet, '课程表')
  const formatted = XLSX.utils.aoa_to_sheet([['比例', '日期'], [0.25, 45292]])
  formatted.A2.z = '0.00%'
  formatted.B2.z = 'yyyy-mm-dd'
  XLSX.utils.book_append_sheet(wb, formatted, '格式')
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['序号'], ...Array.from({ length: 51 }, (_, i) => [i + 1])]), '分页')
  return Buffer.from(XLSX.write(wb, { type: 'buffer', bookType: type === 'xls' ? 'biff8' : 'xlsx' }))
}

async function file(page: Page, type: string, body: Buffer | string) {
  const headers: string[] = []
  await page.route('**/api/v1/documents/sheet-fixture', r => r.fulfill({ json: { id: 'sheet-fixture', filename: `lesson.${type}`, file_type: type, status: 'ready', total_chunks: 1 } }))
  await page.route('**/api/v1/documents/sheet-fixture/chunks', r => r.fulfill({ json: { chunks: [{ id: 'sheet-section', index: 0, text: '课程表资料', page_range: 'p1' }] } }))
  await page.route('**/api/v1/documents/sheet-fixture/raw', r => { headers.push(r.request().headers().authorization); return r.fulfill({ body }) })
  return headers
}
const cells = (page: Page) => page.locator('.ant-tabs-tabpane-active .ant-table-tbody tr[data-row-key]').first().locator('td')

for (const type of ['xlsx', 'xls'] as const) {
  test(`protected ${type} preserves duplicate/blank/numeric/reserved headers and Unicode cells`, async ({ page }) => {
    const headers = await file(page, type, workbook(type))
    await page.goto('/materials/sheet-fixture')
    await expect(cells(page)).toHaveText(['生物', '化学', '空头值', '数字头', '原键值', '<img src=x onerror="window.sheetExecuted=true">'])
    await expect(page.locator('.ant-tabs-tabpane-active thead th')).toHaveText(['学科', '学科', '列3', '123', '_key', '__proto__'])
    await expect(page.locator('.ant-table img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { sheetExecuted?: boolean }).sheetExecuted)).toBeUndefined()
    expect(headers.length).toBeGreaterThan(0)
    expect(headers.every(h => h === `Bearer ${E2E_TOKEN}`)).toBe(true)
  })
}

test('workbook formats dates/percentages and preserves multi-sheet pagination', async ({ page }) => {
  await file(page, 'xlsx', workbook())
  await page.goto('/materials/sheet-fixture')
  await page.getByRole('tab', { name: '格式', exact: true }).click()
  await expect(cells(page)).toHaveText(['25.00%', '2024-01-01'])
  await page.getByRole('tab', { name: '分页', exact: true }).click()
  await expect(page.locator('.ant-tabs-tabpane-active tr[data-row-key]')).toHaveCount(50)
  await page.locator('.ant-tabs-tabpane-active .ant-pagination-item-2').click()
  await expect(cells(page)).toHaveText(['51'])
})

test('UTF8 quoted CSV preserves leading zero/exponent/date text and newlines', async ({ page }) => {
  const source = '\ufeff编号;说明;数值;日期\r\n00123;"中文，带分隔;\n第二行和""引号""";1e3;2024-01-01\r\n'
  await file(page, 'csv', source)
  await page.goto('/materials/sheet-fixture')
  await expect(cells(page)).toHaveText(['00123', '中文，带分隔;\n第二行和"引号"', '1e3', '2024-01-01'])
})

test('URL switch cancels old read, guards its late result and respects height', async ({ page }) => {
  await page.route('**/sheet-current', r => r.fulfill({ body: '学科,说明\n生物,当前资料' }))
  await page.goto('/dashboard')
  await page.evaluate(async () => {
    const original = window.fetch.bind(window)
    const state = window as unknown as { oldSheetStarted: boolean; oldSheetAborted: boolean; releaseSheet: () => void }
    window.fetch = (input, init) => {
      if (String(input).endsWith('/sheet-old')) {
        state.oldSheetStarted = true
        init?.signal?.addEventListener('abort', () => { state.oldSheetAborted = true })
        return new Promise(resolve => { state.releaseSheet = () => resolve(new Response('学科,说明\n旧文件,迟到资料')) })
      }
      return original(input, init)
    }
    ;(await import('/.tmp/sheetHarness.tsx')).show('/sheet-old')
  })
  await expect.poll(() => page.evaluate(() => (window as unknown as { oldSheetStarted?: boolean }).oldSheetStarted)).toBe(true)
  await page.evaluate(async () => (await import('/.tmp/sheetHarness.tsx')).show('/sheet-current'))
  await expect(page.locator('#sheet-test-mount')).toContainText('当前资料')
  await expect.poll(() => page.evaluate(() => (window as unknown as { oldSheetAborted?: boolean }).oldSheetAborted)).toBe(true)
  await page.evaluate(async () => { (window as unknown as { releaseSheet: () => void }).releaseSheet(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
  await expect(page.locator('#sheet-test-mount')).not.toContainText('迟到资料')
  await expect(page.locator('#sheet-test-mount .ant-table-body')).toHaveCSS('max-height', '321px')
})

test('protected file failure retries using current Bearer; empty sheet stays empty', async ({ page }) => {
  await file(page, 'csv', '学科,说明\n生物,重试资料')
  let reads = 0
  await page.route('**/api/v1/documents/sheet-fixture/raw', r => {
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    return ++reads === 1 ? r.fulfill({ status: 401 }) : r.fulfill({ body: '学科,说明\n生物,重试资料' })
  })
  await page.goto('/materials/sheet-fixture')
  await expect(page.getByRole('button', { name: '重新加载原文件' })).toBeVisible()
  await page.getByRole('button', { name: '重新加载原文件' }).click()
  await expect(cells(page)).toHaveText(['生物', '重试资料'])
  expect(reads).toBe(2)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([]), '空表')
  await page.route('**/sheet-empty', r => r.fulfill({ body: Buffer.from(XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' })) }))
  await page.goto('/dashboard')
  await page.evaluate(async () => (await import('/.tmp/sheetHarness.tsx')).show('/sheet-empty', 'xlsx'))
  await expect(page.locator('#sheet-test-mount .ant-empty').first()).toBeVisible()
  await expect(page.locator('#sheet-test-mount tr[data-row-key]')).toHaveCount(0)
})
