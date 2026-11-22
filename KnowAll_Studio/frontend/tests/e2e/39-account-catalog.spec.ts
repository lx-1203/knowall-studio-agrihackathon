import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { stubBackend } from './fixtures'

const doc = (id: string) => ({ id, filename: `${id}-private.md`, file_type: 'md', status: 'ready', page_count: 1, created_at: new Date(0).toISOString() })
async function login(page: Page, id: string) {
  await page.getByLabel('账号', { exact: true }).fill(id)
  await page.getByLabel('密码', { exact: true }).fill('temporary-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.product-account')).toHaveText(id)
}
async function stores(page: Page, command: 'seed' | 'read' | 'rotate' | 'logout') {
  return page.evaluate(async action => {
    // Test-only Vite module import: actual store, no debug bridge added to product.
    const { useAuthStore: auth, useAppStore: app } = await import('/src/stores/index.ts')
    if (action === 'seed') app.setState({ documents: [{ id: 'A', filename: 'A-private.md' }], trees: [{ tree_id: 'A-tree' }], questions: [{ id: 'A-q' }], selectedDoc: 'A', selectedTree: 'A-tree', loading: true, error: 'old-error' })
    if (action === 'rotate') auth.getState().login('rotated-token', { ...auth.getState().user, username: 'updated-name' })
    if (action === 'logout') auth.getState().logout()
    const state = app.getState()
    return { documents: state.documents, trees: state.trees, questions: state.questions, selectedDoc: state.selectedDoc, selectedTree: state.selectedTree, loading: state.loading, error: state.error, actions: typeof state.setDocuments }
  }, command)
}
test.beforeEach(async ({ page }) => {
  await stubBackend(page)
  await page.route('**/api/v1/auth/local-session', r => r.fulfill({ json: { enabled: false } }))
  await page.route('**/api/v1/oauth/providers', r => r.fulfill({ json: { providers: [] } }))
  await page.route('**/api/v1/auth/login', r => {
    const id = r.request().postDataJSON().username
    return r.fulfill({ json: { access_token: `${id}-token`, user: { id, username: id, email: `${id}@example.test` } } })
  })
  await page.setViewportSize({ width: 1600, height: 950 })
})

test('successful empty catalog is empty rather than failure', async ({ page }) => {
  await page.goto('/upload'); await login(page, 'A')
  await expect(page.getByText('暂无资料，请上传你的第一个文档', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toBeHidden()
})
test('same-account token and profile refresh keeps app data and disk notes', async ({ page }) => {
  await page.goto('/dashboard'); await login(page, 'A'); await stores(page, 'seed')
  await page.evaluate(() => localStorage.setItem('round16-reading-note', 'keep-this'))
  const state = await stores(page, 'rotate')
  expect(state.selectedDoc).toBe('A'); expect(state.trees).toHaveLength(1); expect(state.actions).toBe('function')
  expect(await page.evaluate(() => localStorage.getItem('round16-reading-note'))).toBe('keep-this')
})
test('logout synchronously clears app data through native Zustand while retaining actions and disk notes', async ({ page }) => {
  await page.goto('/dashboard'); await login(page, 'A'); await stores(page, 'seed')
  await page.evaluate(() => localStorage.setItem('round16-reading-note', 'keep-this'))
  expect(await stores(page, 'logout')).toEqual({ documents: [], trees: [], questions: [], selectedDoc: null, selectedTree: null, loading: false, error: null, actions: 'function' })
  expect(await page.evaluate(() => localStorage.getItem('round16-reading-note'))).toBe('keep-this')
})
test('real logout and B login never render A catalog or selected source during B loading', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let bRead = false
  await page.route('**/api/v1/documents/?*', async r => {
    if (r.request().headers().authorization === 'Bearer B-token') { bRead = true; await gate; await r.fulfill({ json: [doc('B')] }).catch(() => {}); return }
    await r.fulfill({ json: [doc('A')] })
  })
  await page.goto('/upload'); await login(page, 'A'); await expect(page.getByText('A-private.md', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^勾\s*选$/ }).click(); await expect(page.getByRole('button', { name: '返回当前资料' })).toBeVisible()
  await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, 'B'); await expect.poll(() => bRead).toBe(true)
  await expect(page.getByText('A-private.md', { exact: true })).toBeHidden(); await expect(page.getByRole('button', { name: '返回当前资料' })).toBeHidden()
  release(); await expect(page.getByText('B-private.md', { exact: true })).toBeVisible()
})
test('late catalog A response cannot overwrite B after actual account switch', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let aRead = false; let ended = false
  await page.route('**/api/v1/documents/?*', async r => {
    if (r.request().headers().authorization === 'Bearer A-token') { aRead = true; await gate; await r.fulfill({ json: [doc('A')] }).catch(() => {}); ended = true; return }
    await r.fulfill({ json: [doc('B')] })
  })
  await page.goto('/upload'); await login(page, 'A'); await expect.poll(() => aRead).toBe(true)
  await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, 'B'); await expect(page.getByText('B-private.md', { exact: true })).toBeVisible()
  release(); await expect.poll(() => ended).toBe(true)
  await expect(page.getByText('A-private.md', { exact: true })).toBeHidden(); await expect(page.getByText('B-private.md', { exact: true })).toBeVisible()
})
test('503 safe read budget ends in visible failure and one explicit recovery', async ({ page }) => {
  let reads = 0; let ready = false
  await page.route('**/api/v1/documents/?*', r => { reads++; return ready ? r.fulfill({ json: [doc('A')] }) : r.fulfill({ status: 503, json: { detail: 'private server trace token-secret' } }) })
  await page.goto('/upload'); await login(page, 'A'); await expect.poll(() => reads).toBe(3)
  await expect(page.getByRole('alert')).toContainText('资料列表加载失败'); await expect(page.getByText('暂无资料，请上传你的第一个文档', { exact: true })).toBeHidden()
  await expect(page.getByText(/private server trace/)).toBeHidden(); ready = true
  await page.getByRole('button', { name: '重新加载资料', exact: true }).click(); await expect(page.getByText('A-private.md', { exact: true })).toBeVisible(); expect(reads).toBe(4)
})
test('403 is a single read with safe feedback instead of empty', async ({ page }) => {
  let reads = 0; await page.route('**/api/v1/documents/?*', r => { reads++; return r.fulfill({ status: 403, json: { detail: 'trace-secret' } }) })
  await page.goto('/upload'); await login(page, 'A'); await expect(page.getByRole('alert')).toContainText('当前账号无权访问')
  expect(reads).toBe(1); await expect(page.getByText('暂无资料，请上传你的第一个文档', { exact: true })).toBeHidden()
})
test('401 catalog keeps native logout behavior and clears selected source', async ({ page }) => {
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ json: [doc('A')] }))
  await page.goto('/upload'); await login(page, 'A'); await expect(page.getByText('A-private.md', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^勾\s*选$/ }).click()
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ status: 401, json: { detail: 'expired' } }))
  await page.getByRole('button', { name: '刷新资料', exact: true }).click(); await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  expect((await stores(page, 'read')).selectedDoc).toBeNull()
})
test('native refresh pending blocks duplicates and leaving cancels the read', async ({ page }) => {
  let reads = 0; let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let failed = false
  page.on('requestfailed', req => { if (req.url().includes('/documents/?')) failed = true })
  await page.route('**/api/v1/documents/?*', async r => { reads++; if (reads === 1) return r.fulfill({ json: [doc('A')] }); await gate; await r.fulfill({ json: [doc('late')] }).catch(() => {}) })
  await page.goto('/upload'); await login(page, 'A'); await expect(page.getByText('A-private.md', { exact: true })).toBeVisible()
  const refresh = page.getByRole('button', { name: '刷新资料', exact: true }); await expect(refresh).toBeVisible(); await refresh.click(); await expect.poll(() => reads).toBe(2); await expect(refresh).toBeDisabled()
  await refresh.evaluate((el: HTMLButtonElement) => { el.click(); el.click() }); expect(reads).toBe(2)
  await page.getByRole('link', { name: '我的资料', exact: true }).click(); await expect.poll(() => failed).toBe(true); release()
  expect((await stores(page, 'read')).documents.some((d: { id: string }) => d.id === 'late')).toBe(false)
})
test('late delete from A cannot resurrect A global catalog in B', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let deleting = false; let ended = false
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ json: r.request().headers().authorization === 'Bearer B-token' ? [doc('B')] : [doc('A'), doc('A-other')] }))
  await page.route('**/api/v1/documents/A', async r => { deleting = true; await gate; await r.fulfill({ json: {} }).catch(() => {}); ended = true })
  await page.goto('/upload'); await login(page, 'A'); await expect(page.getByText('A-private.md', { exact: true })).toBeVisible()
  await page.getByRole('row').filter({ hasText: 'A-private.md' }).locator('button.ant-btn-dangerous').click(); await page.getByRole('button', { name: /^确\s*定$/ }).click(); await expect.poll(() => deleting).toBe(true)
  await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, 'B'); await expect(page.getByText('B-private.md', { exact: true })).toBeVisible()
  release(); await expect.poll(() => ended).toBe(true); await expect(page.getByText('A-other-private.md', { exact: true })).toBeHidden(); await expect(page.getByText('B-private.md', { exact: true })).toBeVisible()
})
test('late summary result from A cannot navigate the B account to an old source', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let generating = false; let ended = false
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ json: r.request().headers().authorization === 'Bearer B-token' ? [doc('B')] : [doc('A')] }))
  await page.route('**/api/v1/knowledge/summary/generate', async r => { generating = true; await gate; await r.fulfill({ json: { summary_id: 'A-old-summary', node_count: 1 } }).catch(() => {}); ended = true })
  await page.goto('/upload'); await login(page, 'A'); await expect(page.getByText('A-private.md', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^勾\s*选$/ }).click(); await page.getByRole('button', { name: /生成知识总纲/ }).click(); await expect.poll(() => generating).toBe(true)
  await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, 'B'); await expect(page.getByText('B-private.md', { exact: true })).toBeVisible()
  release(); await expect.poll(() => ended).toBe(true); await expect(page).toHaveURL(/\/upload$/)
})
test('upload cancels an older catalog read before applying its new document list', async ({ page }) => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve }); let reads = 0; let ended = false
  await page.route('**/api/v1/documents/?*', async r => { reads++; if (reads === 1) { await gate; await r.fulfill({ json: [] }).catch(() => {}); ended = true; return } await r.fulfill({ json: [doc('uploaded')] }) })
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'uploaded', filename: 'uploaded-private.md', total_chunks: 1, status: 'ready' } }))
  await page.goto('/upload'); await login(page, 'A'); await expect.poll(() => reads).toBe(1)
  await page.locator('input[type=file]').setInputFiles({ name: 'upload.md', mimeType: 'text/markdown', buffer: Buffer.from('new source') })
  await expect(page.getByText('uploaded-private.md', { exact: true })).toBeVisible(); release(); await expect.poll(() => ended).toBe(true)
  await expect(page.getByText('uploaded-private.md', { exact: true })).toBeVisible(); await expect(page.getByText('暂无资料，请上传你的第一个文档', { exact: true })).toBeHidden()
})
