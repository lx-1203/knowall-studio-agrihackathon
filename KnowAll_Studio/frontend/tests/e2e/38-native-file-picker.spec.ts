import { test, expect, E2E_TOKEN } from './fixtures'
const original = { name: '中文 资料 space.md', mimeType: 'text/markdown', buffer: Buffer.from('# 原始上传正文\n中文 空格路径。') }
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    // Simulates desktop metadata only; any use of the retired path RPC fails the test.
    ;(window as any).__pathCalls = 0
    ;(window as any).electronAPI = { isElectron: true, platform: 'win32', selectFile: async () => { (window as any).__pathCalls++; throw Error('旧文件路径接口') } }
  })
  await page.setViewportSize({ width: 1600, height: 950 })
})
async function choose(page: any, keyboard = false) {
  const pending = page.waitForEvent('filechooser', { timeout: 10000 })
  const button = page.getByRole('button', { name: /选择文件$/ })
  if (keyboard) { await button.focus(); await button.press('Enter') } else await button.click()
  return pending
}
for (const keyboard of [false, true]) {
  test(`${keyboard ? 'Enter' : 'mouse'} chooses original file through native single input with account multipart`, async ({ page }) => {
    let writes = 0; let indexes = 0
    await page.route('**/api/v1/documents/upload', r => {
      writes++; expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
      const body = r.request().postDataBuffer()?.toString() || ''
      expect(body).toContain('name="file"'); expect(body).toContain(original.name); expect(body).toContain('中文 空格路径。')
      return r.fulfill({ json: { document_id: 'native-file', filename: original.name, total_chunks: 1, status: 'ready' } })
    })
    await page.route('**/api/v1/search/index?*', r => { indexes++; return r.fulfill({ json: { indexed: 1 } }) })
    await page.goto('/upload'); const chooser = await choose(page, keyboard); expect(chooser.isMultiple()).toBe(false); await chooser.setFiles(original)
    await expect(page.getByText('已自动建立向量索引', { exact: true })).toBeVisible()
    expect(writes).toBe(1); expect(indexes).toBe(1); await expect(page.locator('input[type=file]')).toHaveCount(1)
    expect(await page.evaluate(() => (window as any).__pathCalls)).toBe(0)
  })
}
test('cancel native selection writes nothing and next explicit selection can proceed', async ({ page }) => {
  let writes = 0; await page.route('**/api/v1/documents/upload', r => { writes++; return r.fulfill({ json: { document_id: 'native-file', filename: original.name, total_chunks: 1, status: 'ready' } }) })
  await page.goto('/upload'); const cancelled = await choose(page); await cancelled.setFiles([])
  expect(writes).toBe(0); await expect(page.getByRole('alert')).toBeHidden()
  const selected = await choose(page); await selected.setFiles(original); await expect.poll(() => writes).toBe(1)
})
test('failed native upload retains feedback and the same file can be chosen again', async ({ page }) => {
  let writes = 0; let ready = false
  await page.route('**/api/v1/documents/upload', r => { writes++; return ready ? r.fulfill({ json: { document_id: 'native-file', filename: original.name, total_chunks: 1, status: 'ready' } }) : r.fulfill({ status: 503, json: { detail: 'temporary native upload failure' } }) })
  await page.goto('/upload'); await (await choose(page)).setFiles(original)
  await expect(page.getByRole('alert')).toContainText('上传失败'); expect(writes).toBe(1)
  ready = true; await (await choose(page)).setFiles(original); await expect(page.getByRole('alert')).toBeHidden(); await expect(page.getByText(/上传成功:/)).toBeVisible(); expect(writes).toBe(2)
})
test('pending upload disables the native choose action and cancellation restores it', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let writes = 0; let choosers = 0
  page.on('filechooser', () => { choosers++ })
  await page.route('**/api/v1/documents/upload', async r => { writes++; await gate; await r.fulfill({ json: { document_id: 'late' } }).catch(() => {}) })
  await page.goto('/upload'); await (await choose(page)).setFiles(original); await expect.poll(() => writes).toBe(1)
  const button = page.getByRole('button', { name: /选择文件$/ }); await expect(button).toBeDisabled()
  await button.evaluate((el: HTMLButtonElement) => { el.click(); el.click() }); expect(choosers).toBe(1); expect(writes).toBe(1)
  await page.getByRole('button', { name: '停止当前上传', exact: true }).click(); await expect(button).toBeEnabled(); release()
})
test('web without Electron bridge gets the same native choose action', async ({ page }) => {
  await page.addInitScript(() => { delete (window as any).electronAPI })
  await page.goto('/upload'); const chooser = await choose(page); await chooser.setFiles([]); await expect(page.locator('input[type=file]')).toHaveCount(1)
})
test('primary material chooser retains classification and opens the chosen source', async ({ page }) => {
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'chosen-source', filename: original.name, status: 'ready' } }))
  let organization: any
  await page.route('**/api/v1/documents/chosen-source/organization', r => { organization = r.request().postDataJSON(); return r.fulfill({ json: {} }) })
  await page.goto('/materials'); await page.getByRole('button', { name: /上传资料$/ }).first().click(); await page.getByLabel('上传资料科目', { exact: true }).fill('生物')
  const pending = page.waitForEvent('filechooser'); await page.getByRole('dialog').locator('.ant-upload-drag').click(); await (await pending).setFiles(original)
  await expect(page).toHaveURL(/\/materials\/chosen-source$/); expect(organization).toEqual({ subject: '生物', material_kind: 'knowledge' })
})
