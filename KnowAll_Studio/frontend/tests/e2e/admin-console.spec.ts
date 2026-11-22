/** Explicit synthetic API fixtures: these browser tests never call a real backend or model. */
import { test, expect } from './fixtures'
import type { Page, Request } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const prefix = '/api/v1/admin/console'
const stamp = '2026-10-19T04:00:00Z'
const overview = (days = 7) => ({
  totals: { users: 1234, active_users: 1200, documents: 321, questions: 678, flashcards: 250, reviews: 456 },
  period: { days, calls: 42, successes: 40, tokens: 9000, cost: 0.12, avg_duration_ms: 1250 },
  daily: [{ date: '2026-10-18', calls: 20, successes: 19, tokens: 4000 }, { date: '2026-10-19', calls: 22, successes: 21, tokens: 5000 }],
  models: [{ model: 'synthetic-deepseek', calls: 42, tokens: 9000 }],
  document_types: [{ type: 'pdf', count: 300 }, { type: 'docx', count: 21 }],
  learning: { attempts: 100, correct: 85, accuracy: 85 }, generated_at: stamp,
})
const user = { id: 'u-1', username: '测试学生', email: 'student@example.test', role: 'user', is_active: true, created_at: stamp, document_count: 2, question_count: 3 }
const doc = { id: 'd-1', filename: '测试课程.pdf', file_type: 'pdf', file_size: 2048, page_count: 12, status: 'completed', user_id: 'u-1', owner: '测试学生', created_at: stamp }
const question = { id: 'q-1', question_text: '合成验收题目', question_type: 'short_answer', difficulty: 'medium', answer: '原始答案', analysis: '原始解析', options: [], tags: ['合成'], user_id: 'u-1', document_id: 'd-1', created_at: stamp }
const key = { id: 'key-1', provider: 'deepseek', key_alias: '合成测试密钥', key_masked: 'sk-****demo', is_active: true, permission_level: 'personal' }
const logs = [
  { id: 'l-1', model_name: 'synthetic-success', task_type: 'chat', tokens_input: 100, tokens_output: 20, cost_estimate: 0, duration_ms: 120, from_cache: true, success: true, created_at: stamp },
  { id: 'l-2', model_name: 'synthetic-failure', task_type: 'summary', tokens_input: 50, tokens_output: 0, cost_estimate: 0, duration_ms: 220, from_cache: false, success: false, created_at: stamp },
]

async function backend(page: Page) {
  const requests: Request[] = []
  let keys = [key], users = [user], documents = [doc], questions = [question]
  let models = { model: 'synthetic-model', fallback_models: ['synthetic-backup'], daily_token_limit: 10000 }
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.slice(prefix.length), method = request.method()
    if (!url.pathname.startsWith(prefix)) return route.fulfill({ json: url.pathname.endsWith('/auth/local-session') ? { enabled: false } : [] })
    requests.push(request)
    const payload = request.postDataJSON()
    let json: unknown = {}
    if (path === '/access') json = { is_admin: true }
    else if (path === '/overview') json = overview(Number(url.searchParams.get('days')) || 7)
    else if (path === '/keys' && method === 'GET') json = keys
    else if (path === '/keys' && method === 'POST') { keys = [...keys, { ...key, id: 'key-2', key_alias: payload.key_alias }]; json = { id: 'key-2' } }
    else if (path.endsWith('/toggle')) { keys = keys.map(item => item.id === path.split('/')[2] ? { ...item, is_active: !item.is_active } : item); json = { success: true } }
    else if (path.startsWith('/keys/') && method === 'DELETE') { keys = keys.filter(item => item.id !== path.split('/')[2]); json = { success: true } }
    else if (path === '/models') { if (method === 'POST') models = payload; json = models }
    else if (path === '/logs') json = logs
    else if (path === '/health') json = { status: 'ok', database: 'ok', vector_store: { status: 'ok' }, version: 'synthetic', timestamp: stamp }
    else if (path === '/cache/stats') json = { total_entries: 17, total_tokens_saved: 500 }
    else if (path === '/quota/status') json = { daily_limit: 10000, used_today: 100, remaining: 9900 }
    else if (path === '/cache/clear') json = { cleared: 17 }
    else if (/^\/(users|documents|questions)$/.test(path)) {
      const source = path === '/users' ? users : path === '/documents' ? documents : questions
      const search = url.searchParams.get('search') || ''
      const items = source.filter(item => JSON.stringify(item).includes(search))
      json = { items, total: items.length, page: Number(url.searchParams.get('page') || 1), page_size: Number(url.searchParams.get('page_size') || 10) }
    } else if (path.startsWith('/users/')) { users = users.map(item => ({ ...item, ...payload })); json = users[0] }
    else if (path.startsWith('/documents/')) { if (method !== 'GET') documents = documents.map(item => ({ ...item, ...payload })); json = documents[0] }
    else if (path.startsWith('/questions/')) { if (method !== 'GET') questions = questions.map(item => ({ ...item, ...payload })); json = questions[0] }
    else throw new Error(`Unstubbed admin request: ${method} ${path}`)
    return route.fulfill({ json })
  })
  return requests
}
async function openTab(page: Page, label: string) {
  const tab = page.getByRole('tab', { name: new RegExp(`${label}$`) })
  const fits = await tab.evaluate(element => {
    const bounds = element.getBoundingClientRect(), nav = element.closest('.ant-tabs-nav-wrap')?.getBoundingClientRect()
    return !nav || bounds.left >= nav.left && bounds.right <= nav.right
  })
  if (fits) await tab.click()
  else {
    await page.locator('.ant-tabs-nav-more').hover()
    await page.getByRole('option', { name: new RegExp(`${label}$`) }).click()
  }
}

test('403 access blocks console and prevents business reads', async ({ page }) => {
  const calls = await backend(page)
  await page.route(`**${prefix}/access`, route => route.fulfill({ status: 403, json: { detail: '需要管理员权限' } }))
  await page.goto('/admin/')
  await expect(page.getByText('无法验证管理员权限', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '管理控制台', exact: true })).toHaveCount(0)
  await expect(page.getByRole('tab')).toHaveCount(0)
  expect(calls).toHaveLength(0)
})

test('non-admin account sees an actionable permission explanation', async ({ page }) => {
  const calls = await backend(page)
  await page.route(`**${prefix}/access`, route => route.fulfill({ json: { is_admin: false } }))
  await page.goto('/admin/')
  await expect(page.getByText('需要管理员权限', { exact: true })).toBeVisible()
  await expect(page.getByRole('tab')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '返回学习空间', exact: true })).toHaveCount(0)
  expect(calls).toHaveLength(0)
})

test('admin entry loads no learner app modules, local session or sync, and logout uses admin login', async ({ page }) => {
  const urls: string[] = [], sockets: string[] = []
  page.on('request', request => urls.push(request.url()))
  page.on('websocket', socket => sockets.push(socket.url()))
  await backend(page); await page.goto('/admin/')
  await expect(page.getByRole('heading', { name: '管理控制台', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '学习导航' })).toHaveCount(0)
  await expect(page.locator('a[href="/materials"], a[href="/quiz"], a[href="/flashcards"], a[href="/study"]')).toHaveCount(0)
  expect(urls.filter(url => /\/src\/App\.tsx|\/src\/pages\/(?!AdminPage\.tsx|admin\/)|\/auth\/local-session/.test(url))).toEqual([])
  // Vite's development HMR socket is expected; learner sync must never connect.
  expect(sockets.filter(url => new URL(url).pathname !== '/')).toEqual([])
  await page.getByRole('button', { name: '退出管理端', exact: true }).click()
  await expect(page.getByRole('heading', { name: '登录管理控制台', exact: true })).toBeVisible()
  await expect(page.getByLabel('管理员账号', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '登录管理端', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /注册|学习空间/ })).toHaveCount(0)
})

test('overview renders synthetic totals and switches reporting range', async ({ page }) => {
  const calls = await backend(page)
  await page.goto('/admin/')
  await expect(page.getByRole('heading', { name: '管理控制台', exact: true })).toBeVisible()
  await expect(page.locator('.admin-metric').filter({ hasText: '注册用户' })).toContainText('1,234')
  await expect(page.locator('.admin-metric').filter({ hasText: '资料总量' })).toContainText('321')
  await expect(page.getByRole('img', { name: '近7天调用量，共42次', exact: true })).toBeVisible()
  await expect(page.getByText('更新于', { exact: false })).toContainText('2026/10/19')
  await page.getByText('近30天', { exact: true }).click()
  await expect(page.getByRole('img', { name: '近30天调用量，共42次', exact: true })).toBeVisible()
  expect(calls.some(r => r.url().includes('days=30'))).toBe(true)
})

test('logs search and status filter displayed results', async ({ page }) => {
  await backend(page); await page.goto('/admin/'); await openTab(page, '调用日志')
  await expect(page.getByText('synthetic-success', { exact: true })).toBeVisible()
  await page.getByLabel('搜索调用日志', { exact: true }).fill('failure')
  await expect(page.getByText('synthetic-success', { exact: true })).toHaveCount(0)
  await expect(page.getByText('synthetic-failure', { exact: true })).toBeVisible()
  await page.getByLabel('搜索调用日志', { exact: true }).fill('')
  await page.getByText('全部状态', { exact: true }).click()
  await page.getByText('调用成功', { exact: true }).click()
  await expect(page.getByText('synthetic-failure', { exact: true })).toHaveCount(0)
  await expect(page.getByText('synthetic-success', { exact: true })).toBeVisible()
})

test('key add toggle delete and model save issue correct mutations', async ({ page }) => {
  const calls = await backend(page); await page.goto('/admin/'); await openTab(page, '模型与密钥')
  await expect(page.getByText('sk-****demo', { exact: true })).toBeVisible()
  await page.getByRole('switch', { name: '合成测试密钥密钥状态' }).click()
  await expect(page.getByRole('switch', { name: '合成测试密钥密钥状态' })).not.toBeChecked()
  await page.getByRole('button', { name: /添加密钥$/ }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('密钥名称', { exact: true }).fill('新测试密钥')
  await dialog.getByLabel('API 密钥', { exact: true }).fill('sk-synthetic-never-real')
  await dialog.getByRole('button', { name: '保存密钥', exact: true }).click()
  await expect(page.getByText('新测试密钥', { exact: true })).toBeVisible()
  await expect(page.getByText('sk-synthetic-never-real', { exact: true })).toHaveCount(0)
  await page.getByRole('row').filter({ hasText: '新测试密钥' }).getByRole('button', { name: /删除$/ }).click()
  await expect.poll(() => calls.filter(r => r.method() === 'DELETE')).toHaveLength(0)
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.getByText('新测试密钥', { exact: true })).toHaveCount(0)
  await page.getByLabel('默认模型', { exact: true }).fill('synthetic-new-model')
  await page.getByRole('button', { name: '保存模型配置', exact: true }).click()
  await expect(page.getByText('模型配置已保存', { exact: true })).toBeVisible()
  expect(calls.find(r => r.method() === 'POST' && r.url().endsWith('/models'))?.postDataJSON().model).toBe('synthetic-new-model')
})

test('overview error can retry and empty results remain explicit', async ({ page }) => {
  await backend(page)
  let failing = true
  await page.route(`**${prefix}/overview?*`, route => route.fulfill(failing ? { status: 403, json: { detail: '模拟暂时不可用' } } : { json: { ...overview(), period: { ...overview().period, calls: 0 }, models: [], document_types: [] } }))
  await page.goto('/admin/')
  await expect(page.getByText('概览加载失败', { exact: true })).toBeVisible()
  failing = false
  await page.getByRole('button', { name: '重新加载', exact: true }).click()
  await expect(page.getByText('所选时间范围内暂无调用记录', { exact: true })).toBeVisible()
  await expect(page.getByText('暂无模型调用', { exact: true })).toBeVisible()
  await expect(page.getByText('暂无上传资料', { exact: true })).toBeVisible()
})

test('invalid stored model config can be replaced from the visible form', async ({ page }) => {
  await backend(page)
  let saved: Record<string, unknown> | null = null
  await page.route(`**${prefix}/models`, route => {
    if (route.request().method() === 'POST') saved = route.request().postDataJSON()
    return route.fulfill(saved ? { json: saved } : { status: 500, json: { detail: '模拟历史配置格式无效' } })
  })
  await page.goto('/admin/'); await openTab(page, '模型与密钥')
  await expect(page.getByText('读取失败，仍可填写并保存新配置进行修复。保存会替换已有配置记录。', { exact: true })).toBeVisible()
  await page.getByLabel('默认模型', { exact: true }).fill('synthetic-repaired')
  await page.getByLabel('备用模型', { exact: true }).fill('synthetic-backup')
  await page.getByRole('button', { name: '保存模型配置', exact: true }).click()
  await expect(page.getByText('模型配置已保存', { exact: true })).toBeVisible()
  await expect(page.getByLabel('默认模型', { exact: true })).toHaveValue('synthetic-repaired')
  expect(saved).toMatchObject({ model: 'synthetic-repaired', fallback_models: ['synthetic-backup'] })
})

test('business search, confirmation, document details and question editing persist through API', async ({ page }) => {
  const calls = await backend(page); await page.goto('/admin/'); await openTab(page, '用户管理')
  await expect(page.getByText('测试学生', { exact: true })).toBeVisible()
  await page.getByLabel('搜索用户', { exact: true }).fill('student@example.test')
  await page.getByLabel('搜索用户', { exact: true }).press('Enter')
  await expect.poll(() => calls.some(r => new URL(r.url()).searchParams.get('search') === 'student@example.test')).toBe(true)
  await page.getByRole('button', { name: /^停\s*用$/ }).click()
  expect(calls.filter(r => r.method() === 'PATCH')).toHaveLength(0)
  await page.getByRole('button', { name: '确认停用', exact: true }).click()
  await expect(page.getByRole('button', { name: /^启\s*用$/ })).toBeVisible()
  expect(calls.find(r => r.method() === 'PATCH')?.postDataJSON()).toEqual({ is_active: false })
  await openTab(page, '资料管理')
  await page.getByRole('button', { name: /^详\s*情$/ }).click()
  await expect(page.getByRole('dialog').getByText('2.0 KB', { exact: true })).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: /^关\s*闭$/ }).click()
  await page.getByRole('button', { name: '重命名', exact: true }).click()
  await page.getByRole('dialog').getByLabel('资料名称', { exact: true }).fill('已重命名课程.pdf')
  await page.getByRole('dialog').getByRole('button', { name: /^保\s*存$/ }).click()
  await expect(page.getByText('已重命名课程.pdf', { exact: true })).toBeVisible()
  await openTab(page, '题库管理')
  await page.getByRole('button', { name: /^编\s*辑$/ }).click()
  const edit = page.getByRole('dialog')
  await edit.getByLabel('题干', { exact: true }).fill('修改后的合成题目')
  await edit.getByLabel('答案', { exact: true }).fill('修改后的答案')
  await edit.getByLabel('解析', { exact: true }).fill('修改后的解析')
  await edit.getByRole('button', { name: /^保\s*存$/ }).click()
  await expect(page.getByText('修改后的合成题目', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^详\s*情$/ }).click()
  await expect(page.getByRole('dialog').getByText('修改后的答案', { exact: true })).toBeVisible()
  await expect(page.getByRole('dialog').getByText('修改后的解析', { exact: true })).toBeVisible()
})

test('business read retries and rejected save retains draft', async ({ page }) => {
  await backend(page)
  let readFailure = true, saveFailure = true
  await page.route(`**${prefix}/documents?*`, route => route.fulfill(readFailure ? { status: 403, json: { detail: '合成列表错误' } } : { json: { items: [doc], total: 1, page: 1, page_size: 20 } }))
  await page.route(`**${prefix}/documents/d-1`, route => route.fulfill(saveFailure ? { status: 400, json: { detail: '合成保存错误' } } : { json: { ...doc, filename: '保留输入.pdf' } }))
  await page.goto('/admin/'); await openTab(page, '资料管理')
  await expect(page.getByText('数据加载失败', { exact: true })).toBeVisible()
  readFailure = false
  await page.getByRole('button', { name: /^重\s*试$/ }).click()
  await page.getByRole('button', { name: '重命名', exact: true }).click()
  const edit = page.getByRole('dialog')
  await edit.getByLabel('资料名称', { exact: true }).fill('保留输入.pdf')
  await edit.getByRole('button', { name: /^保\s*存$/ }).click()
  await expect(edit.getByText('合成保存错误', { exact: true })).toBeVisible()
  await expect(edit.getByLabel('资料名称', { exact: true })).toHaveValue('保留输入.pdf')
  saveFailure = false
  await edit.getByRole('button', { name: /^保\s*存$/ }).click()
  await expect(edit).not.toBeVisible()
})

test('current user cannot be disabled and cache clearing requires confirmation', async ({ page }) => {
  const calls = await backend(page)
  await page.route(`**${prefix}/users?*`, route => route.fulfill({ json: { items: [{ ...user, id: 'e2e-user' }], total: 1, page: 1, page_size: 20 } }))
  await page.goto('/admin/'); await openTab(page, '用户管理')
  await expect(page.getByRole('button', { name: /^停\s*用$/ })).toBeDisabled()
  await openTab(page, '系统监控')
  await expect(page.getByText('9,900', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '清除缓存', exact: true }).click()
  expect(calls.filter(r => r.method() === 'DELETE')).toHaveLength(0)
  await page.getByRole('button', { name: '确认清除', exact: true }).click()
  await expect.poll(() => calls.some(r => r.method() === 'DELETE' && r.url().endsWith('/cache/clear'))).toBe(true)
})

test('user pagination requests a server page and replaces rows', async ({ page }) => {
  await backend(page)
  const pages: number[] = []
  await page.route(`**${prefix}/users?*`, route => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get('page') || 1)
    pages.push(pageNumber)
    const indices = pageNumber === 1 ? Array.from({ length: 20 }, (_, index) => index + 1) : [21]
    return route.fulfill({ json: { items: indices.map(index => ({ ...user, id: `u-${index}`, username: `第${index}位用户` })), total: 21, page: pageNumber, page_size: 20 } })
  })
  await page.goto('/admin/'); await openTab(page, '用户管理')
  await expect(page.getByText('第1位用户', { exact: true })).toBeVisible()
  await page.locator('.ant-pagination-item-2').click()
  await expect(page.getByText('第21位用户', { exact: true })).toBeVisible()
  await expect(page.getByText('第1位用户', { exact: true })).toHaveCount(0)
  expect(pages).toEqual([1, 2])
})

for (const theme of ['light', 'dark']) test(`${theme} desktop and narrow console screenshots`, async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem('knowall-theme', value), theme)
  await backend(page); await page.goto('/admin/')
  await expect(page.getByRole('img', { name: '近7天调用量，共42次', exact: true })).toBeVisible()
  const folder = resolve('../../docs/admin-console'); await mkdir(folder, { recursive: true })
  await page.screenshot({ path: resolve(folder, `overview-${theme}.png`), fullPage: true, animations: 'disabled' })
  await openTab(page, '数据大屏')
  await expect(page.getByRole('heading', { name: '智识工坊 · 数据驾驶舱', exact: true })).toBeVisible()
  await page.screenshot({ path: resolve(folder, `wall-${theme}.png`), fullPage: true, animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
  await page.screenshot({ path: resolve(folder, `mobile-${theme}.png`), fullPage: true, animations: 'disabled' })
  for (const tab of ['用户管理', '资料管理', '题库管理', '系统监控', '模型与密钥', '调用日志']) {
    await openTab(page, tab)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
    if (tab === '模型与密钥') {
      await page.getByRole('button', { name: /添加密钥$/ }).click()
      await expect(page.getByRole('dialog', { name: '添加模型密钥' })).toBeVisible()
      await page.getByRole('dialog').getByRole('button', { name: /^取\s*消$/ }).click()
    }
  }
})
