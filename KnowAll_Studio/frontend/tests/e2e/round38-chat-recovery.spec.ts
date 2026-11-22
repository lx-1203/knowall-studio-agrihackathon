import { test, expect, E2E_TOKEN } from './fixtures'
const convs = ['甲对话', '乙对话'].map((title, i) => ({ id: String(i), title, role_preset: 'tutor', created_at: '2026-10-09T19:40:00Z' }))
const roles = { tutor: { name: '学习辅导' } }
const detail = (i: number) => ({ conversation_id: String(i), title: convs[i].title, role_preset: 'tutor', messages: [{ id: 'm' + i, role: 'user', content: i ? '乙原文' : '甲原文', created_at: '' }] })
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/chat/conversations', r => r.fulfill({ json: convs }))
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: roles }))
  await page.route('**/api/v1/chat/conversations/*', r => r.fulfill({ json: detail(Number(r.request().url().split('/').pop())) }))
})
for (const [resource, title, retry] of [
  ['conversations', '对话列表加载失败', '重新加载对话列表'],
  ['roles', '角色加载失败', '重新加载角色'],
]) test(resource + ' failure persists, hides detail, independently recovers without writes', async ({ page }) => {
  let fail = true, reads = 0, writes = 0, other = 0
  await page.route('**/api/v1/chat/assistant**', r => { writes++; return r.fulfill({ status: 400 }) })
  await page.route('**/api/v1/chat/' + (resource === 'roles' ? 'conversations' : 'roles'), r => { other++; return r.fulfill({ json: resource === 'roles' ? convs : roles }) })
  await page.route('**/api/v1/chat/' + resource, r => { reads++; return r.fulfill(fail ? { status: 403, json: { detail: 'PRIVATE_DETAIL_NEVER_RENDER' } } : { json: resource === 'roles' ? roles : convs }) })
  await page.goto('/chat')
  await expect(page.getByText(title, { exact: true })).toBeVisible()
  await expect(page.getByText('PRIVATE_DETAIL_NEVER_RENDER')).toHaveCount(0)
  expect(reads).toBe(1)
  if (resource === 'roles') {
    await expect(page.getByText('甲对话', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  } else await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
  fail = false
  await page.getByRole('button', { name: retry, exact: true }).click()
  await expect(page.getByText(title, { exact: true })).toHaveCount(0)
  await expect.poll(() => reads).toBe(2)
  expect(other).toBe(1); expect(writes).toBe(0)
  await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
})
test('true empty list differs from failed list, empty roles cannot send', async ({ page }) => {
  await page.route('**/api/v1/chat/conversations', r => r.fulfill({ json: [] }))
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: {} }))
  await page.goto('/chat')
  await expect(page.getByText('暂无历史对话', { exact: true })).toBeVisible()
  await expect(page.getByText('暂无可用角色', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  await expect(page.getByText('对话列表加载失败', { exact: true })).toHaveCount(0)
})
test('narrow conversation failure remains readable with retry below its text', async ({ page }) => {
  await page.route('**/api/v1/chat/conversations', r => r.fulfill({ status: 403, json: { detail: 'private' } }))
  await page.goto('/chat')
  const title = page.getByText('对话列表加载失败', { exact: true })
  await expect(title).toBeVisible()
  const box = await title.boundingBox(), retry = await page.getByRole('button', { name: '重新加载对话列表', exact: true }).boundingBox()
  expect(box!.width).toBeGreaterThan(120)
  expect(retry!.y).toBeGreaterThan(box!.y + box!.height)
})
test('pending role does not block loaded list and cannot send', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/chat/roles', async r => { await gate; await r.fulfill({ json: roles }) })
  await page.goto('/chat', { waitUntil: 'domcontentloaded' })
  await expect(page.getByText('甲对话', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  release(); await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
})
test('pending list cannot race a newly generated sidebar conversation', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/chat/conversations', async r => { await gate; await r.fulfill({ json: convs }) })
  await page.goto('/chat', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('.ant-select-selection-item')).toHaveText('学习辅导')
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  release(); await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
})
test('unknown stored role requires an available role selection before sending', async ({ page }) => {
  await page.route('**/api/v1/chat/conversations/0', r => r.fulfill({ json: { ...detail(0), role_preset: 'removed-preset' } }))
  await page.goto('/chat'); await page.getByText('甲对话', { exact: true }).click()
  await expect(page.getByText('甲原文', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  await page.getByRole('combobox', { name: '助教角色', exact: true }).click()
  await page.getByTitle('学习辅导', { exact: true }).click()
  await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
})
test('stored available role is restored and a synthetic reply writes selected conversation once', async ({ page }) => {
  let writes = 0
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { ...roles, mentor: { name: '研究导师' } } }))
  await page.route('**/api/v1/chat/conversations/0', r => r.fulfill({ json: { ...detail(0), role_preset: 'mentor' } }))
  await page.route('**/api/v1/chat/assistant', r => {
    writes++; expect(r.request().headers().authorization).toBe('Bearer ' + E2E_TOKEN)
    expect(r.request().postDataJSON()).toMatchObject({ role_preset: 'mentor', conversation_id: '0', message: '继续讨论' })
    return r.fulfill({ json: { message: '明确合成回复', conversation_id: '0' } })
  })
  await page.goto('/chat'); await page.getByText('甲对话', { exact: true }).click()
  await expect(page.getByText('甲原文', { exact: true })).toBeVisible()
  await expect(page.locator('.ant-select-selection-item')).toHaveText('研究导师')
  await page.getByRole('switch').nth(1).click()
  await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('继续讨论')
  await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown')).toHaveText('明确合成回复')
  expect(writes).toBe(1)
})
test('failed detail clears old messages, gates send and retries only selected detail', async ({ page }) => {
  let fail = true, reads = 0, writes = 0
  await page.route('**/api/v1/chat/conversations/1', r => { reads++; return r.fulfill(fail ? { status: 404, json: { detail: 'PRIVATE_DETAIL_NEVER_RENDER' } } : { json: detail(1) }) })
  await page.route('**/api/v1/chat/assistant**', r => { writes++; return r.fulfill({ status: 400 }) })
  await page.goto('/chat')
  await page.getByText('甲对话', { exact: true }).click()
  await expect(page.getByText('甲原文', { exact: true })).toBeVisible()
  await page.getByText('乙对话', { exact: true }).click()
  await expect(page.getByText('历史消息加载失败', { exact: true })).toBeVisible()
  await expect(page.getByText('甲原文', { exact: true })).toHaveCount(0)
  await expect(page.getByText('PRIVATE_DETAIL_NEVER_RENDER')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  fail = false; await page.getByRole('button', { name: '重新加载历史消息', exact: true }).click()
  await expect(page.getByText('乙原文', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
  expect(reads).toBe(2); expect(writes).toBe(0)
})
for (const action of ['switch', 'new', 'leave']) test('pending detail cancelled by ' + action + ' never overwrites next view', async ({ page }) => {
  await page.addInitScript(() => {
    const urls = new WeakMap<XMLHttpRequest, string>(), open = XMLHttpRequest.prototype.open, abort = XMLHttpRequest.prototype.abort
    Object.assign(window, { __aborted: [] as string[] })
    XMLHttpRequest.prototype.open = function (method, url, async = true, user?, password?) { urls.set(this, String(url)); return open.call(this, method, url, async, user, password) }
    XMLHttpRequest.prototype.abort = function () { (window as unknown as { __aborted: string[] }).__aborted.push(urls.get(this) || ''); return abort.call(this) }
  })
  let release!: () => void, requested = false
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/chat/conversations/0', async r => { requested = true; await gate; await r.fulfill({ json: detail(0) }).catch(() => {}) })
  await page.goto('/chat'); await page.getByText('甲对话', { exact: true }).click()
  await expect.poll(() => requested).toBe(true)
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  if (action === 'switch') { await page.getByText('乙对话', { exact: true }).click(); await expect(page.getByText('乙原文', { exact: true })).toBeVisible() }
  else if (action === 'new') await page.getByRole('button', { name: /新对话/ }).click()
  else { await page.getByRole('link', { name: '我的资料', exact: true }).click(); await expect(page).not.toHaveURL(/chat/) }
  await expect.poll(() => page.evaluate(() => (window as unknown as { __aborted: string[] }).__aborted.filter(s => s.endsWith('/chat/conversations/0')).length)).toBe(1)
  release()
  await expect(page.getByText('甲原文', { exact: true })).toHaveCount(0)
  if (action === 'new') await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
})
