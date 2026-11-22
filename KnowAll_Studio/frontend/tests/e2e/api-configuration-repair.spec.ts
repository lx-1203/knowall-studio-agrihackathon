import { test, expect } from './fixtures'

test.setTimeout(60000)
const draft = { name: '我的模型网关', provider: 'custom', protocol: 'openai', base_url: 'https://gateway.test/v1', model: '', is_active: true }
const choices = [{ value: 'connection-demo/vendor/new-release', model_id: 'vendor/new-release', display_name: 'New Release', label: 'New Release (vendor/new-release)' }]
function row(values = {}) { return { ...draft, id: 'demo', model_ref: '', has_key: true, key_masked: '已保存（加密）', models: [], fetched_at: null, last_test: null, ...values } }
const presets = [{ provider: 'custom', name: '自定义 / 中转服务', base_url: '', protocol: 'openai' }, { provider: 'anthropic', name: 'Anthropic / Claude', base_url: 'https://api.anthropic.com/v1', protocol: 'anthropic' }]
const config = (connections: object[], selected = '') => ({ connections, default_model: selected, presets, legacy_keys: [], server_connections: [] })

test('add, fetch models, edit without exposing key, choose default, refresh, disable and remove', async ({ page }) => {
  let rows: ReturnType<typeof row>[] = []
  let selected = ''
  const savedKeys: string[] = []
  let balanceReads = 0
  await page.route('**/api/v1/ai/balance', route => { balanceReads++; return route.fulfill({ json: { status: 'unavailable', balances: [] } }) })
  await page.route('**/api/v1/ai/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/v1/ai/connections') && !path.endsWith('/config') && !path.endsWith('/default')) return route.fallback()
    const body = route.request().postDataJSON()
    if (path.endsWith('/config')) return route.fulfill({ json: config(rows, selected) })
    if (path.endsWith('/models')) { rows[0] = row({ ...rows[0], models: choices, fetched_at: '2026-10-20T12:00:00Z' }); return route.fulfill({ json: { models: choices, errors: [], fetched_at: '2026-10-20T12:00:00Z' } }) }
    if (path.endsWith('/test')) { const result = { status: 'success', message: '模型已返回有效文本，连接和生成测试通过。', model: rows[0].model, checked_at: '2026-10-20T12:00:00Z', latency_ms: 20 }; rows[0] = row({ ...rows[0], last_test: result }); return route.fulfill({ json: result }) }
    if (path.endsWith('/default')) { selected = 'connection-demo/' + body.model; return route.fulfill({ json: { model: selected } }) }
    if (route.request().method() === 'DELETE') { rows = []; return route.fulfill({ json: { status: 'removed' } }) }
    savedKeys.push(body.api_key)
    const { api_key, ...safe } = body
    rows = [row({ ...(rows[0] || {}), ...safe, model_ref: safe.model ? 'connection-demo/' + safe.model : '' })]
    return route.fulfill({ status: route.request().method() === 'POST' ? 201 : 200, json: rows[0] })
  })
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '添加连接', exact: true }).click()
  await page.getByRole('textbox', { name: '连接名称', exact: true }).fill(draft.name)
  await page.getByRole('textbox', { name: 'API 基础地址', exact: true }).fill(draft.base_url)
  await page.getByLabel('API Key', { exact: true }).fill('synthetic-browser-key')
  await page.getByRole('button', { name: '保存连接', exact: true }).click()
  let connection = page.getByRole('article', { name: '模型连接 ' + draft.name })
  await expect(connection).toContainText(draft.base_url)
  await expect(connection).not.toContainText('synthetic-browser-key')
  await connection.getByRole('button', { name: '获取模型', exact: true }).click()
  await expect(connection).toContainText('服务商可用模型：1 个')
  await connection.getByRole('button', { name: /编\s*辑/ }).click()
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
  await page.getByRole('combobox', { name: '模型 ID', exact: true }).fill('vendor/new-release')
  await page.getByRole('button', { name: '保存连接', exact: true }).click()
  await expect(connection).toContainText('vendor/new-release')
  await connection.getByRole('button', { name: '测试生成', exact: true }).click()
  await expect(connection).toContainText('连接和生成测试通过')
  const beforeDefault = balanceReads
  await connection.getByRole('button', { name: '设为默认', exact: true }).click()
  await expect.poll(() => balanceReads).toBeGreaterThan(beforeDefault)
  await expect(page.getByLabel('当前默认模型')).toContainText('我的模型网关 / vendor/new-release')
  await page.reload({ waitUntil: 'domcontentloaded' })
  connection = page.getByRole('article', { name: '模型连接 ' + draft.name })
  await expect(connection).toContainText('已保存（加密）')
  await connection.getByRole('switch').click()
  await expect(page.getByLabel('当前默认模型')).toContainText('已停用')
  await connection.getByRole('button', { name: /移\s*除/ }).click()
  await page.getByRole('button', { name: /移\s*除/ }).last().click()
  await expect(connection).toHaveCount(0)
  expect(savedKeys[0]).toBe('synthetic-browser-key')
  expect(savedKeys.slice(1)).toEqual(['', ''])
})

test('server validation stays visible in editor without clearing the input', async ({ page }) => {
  await page.route('**/api/v1/ai/config', route => route.fulfill({ json: config([]) }))
  await page.route('**/api/v1/ai/connections', route => route.fulfill({ status: 422, json: { detail: '请填写不含账号、密钥或查询参数的 HTTPS 基础地址' } }))
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '添加连接', exact: true }).click()
  await page.getByRole('textbox', { name: '连接名称', exact: true }).fill('错误地址测试')
  await page.getByRole('textbox', { name: 'API 基础地址', exact: true }).fill('https://host.test/v1?key=invalid')
  await page.getByLabel('API Key', { exact: true }).fill('synthetic-browser-key')
  await page.getByRole('button', { name: '保存连接', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('请填写不含账号、密钥或查询参数的 HTTPS 基础地址')
  await expect(page.getByRole('textbox', { name: '连接名称', exact: true })).toHaveValue('错误地址测试')
})

test('material generation sends the selected connection and displays cards', async ({ page }) => {
  const selected = 'connection-demo/vendor/new-release'
  await page.route('**/api/v1/ai/config', route => route.fulfill({ json: config([row({ model: 'vendor/new-release', model_ref: selected })], selected) }))
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/chunks') ? [{ id: 'c1', index: 0, text: '# 细胞\n细胞膜控制物质进出。' }] : { id: 'bio', filename: '生物教材.md', file_type: 'md', status: 'ready' } }))
  let sentModel = ''
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/generate')) { sentModel = route.request().postDataJSON().model; return route.fulfill({ json: { id: 'pack', coverage: { failed_sections: 0, total_sections: 1 }, sections: [{ id: 's', title: '细胞', notes: '细胞膜的作用。', chunk_ids: ['c1'], flashcards: [{ id: 'card', front: '细胞膜的作用是什么？', back: '控制物质进出。' }] }] } }) }
    return route.fulfill({ json: path.endsWith('/preview') ? { sections: [] } : null })
  })
  await page.goto('/materials/bio', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '整理学习内容', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '学习生成模型' })).toHaveValue(selected)
  await page.getByRole('button', { name: '生成学习包', exact: true }).click()
  await expect.poll(() => sentModel).toBe(selected)
  await page.getByRole('tab', { name: '知识卡', exact: true }).click()
  await expect(page.getByText('细胞膜的作用是什么？', { exact: true })).toBeVisible()
})

test('connection settings fit a narrow dark screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => localStorage.setItem('knowall-theme', 'dark'))
  await page.route('**/api/v1/ai/config', route => route.fulfill({ json: config([row({ model: 'vendor/new-release', base_url: 'https://a-very-long-model-provider-address.test/custom/gateway/v1' })]) }))
  await page.goto('/settings', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('article', { name: '模型连接 ' + draft.name })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
