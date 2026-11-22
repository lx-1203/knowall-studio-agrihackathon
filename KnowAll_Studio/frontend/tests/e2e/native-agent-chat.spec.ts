import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const source = { citation_id: 'S1', document_id: 'doc', chunk_id: 'chunk', filename: '细胞生物学教材.pdf', page_range: '12–13', text: '细胞呼吸将有机物中的能量转化为 ATP。', chunk_sha256: 'a'.repeat(64), truncated: false }
const steps = [{ seq: 1, type: 'run_started' }, { seq: 2, type: 'tool_started', tool: 'search_documents', call_id: 'c1' }, { seq: 3, type: 'tool_completed', tool: 'search_documents', call_id: 'c1' }, { seq: 4, type: 'run_completed' }]
const result = { status: 'completed', scope: 'readonly_documents', message: '呼吸作用产生 ATP。[S1]', conversation_id: 'native-conv', sources: [source], steps, model: 'deepseek-chat', usage: { input_tokens: 120, output_tokens: 20 } }
const models = { models: [{ value: 'deepseek/deepseek-chat', label: 'deepseek-chat', model_tool_support: 'unverified' }] }
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/chat/conversations', r => r.fulfill({ json: [] }))
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { tutor: { name: '学习辅导' } } }))
  await page.route('**/api/v1/agent/models', r => r.fulfill({ json: models }))
  await page.route('**/api/v1/agent/answer', r => r.fulfill({ json: result }))
  await page.route('**/api/v1/agent/sources/**', r => r.fulfill({ json: source }))
})
test('explicit Agent mode uses the native contract and shows actual sources and receipts', async ({ page }) => {
  let payload: Record<string, unknown> = {}
  await page.route('**/api/v1/agent/answer', r => { payload = r.request().postDataJSON(); return r.fulfill({ json: result }) })
  await page.goto('/chat')
  await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await expect(page.getByRole('combobox', { name: 'Agent 模型' })).toBeVisible()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('根据资料解释细胞呼吸')
  await page.getByRole('button', { name: /发送$/ }).click()
  expect(payload).toEqual({ message: '根据资料解释细胞呼吸', conversation_id: null, model: 'deepseek/deepseek-chat' })
  await expect(page.locator('.chat-markdown')).toContainText('呼吸作用产生 ATP。[S1]')
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /S1.*细胞生物学教材/ }).click()
  await expect(page.getByRole('dialog')).toContainText(source.text)
  await expect(page.getByRole('dialog')).toContainText('12–13')
})
test('model discovery failure can retry and no configured model links to settings', async ({ page }) => {
  let requests = 0
  await page.route('**/api/v1/agent/models', r => r.fulfill(++requests === 1 ? { status: 503, json: { detail: '配置暂不可用' } } : { json: { models: [] } }))
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await expect(page.getByText('配置暂不可用', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /发送$/ })).toBeDisabled()
  await page.getByRole('button', { name: '重新加载模型' }).click()
  await expect(page.getByRole('link', { name: '配置模型' })).toHaveAttribute('href', '/settings')
  expect(requests).toBe(2)
})
test('changed source is not presented as current text and recovers through retry', async ({ page }) => {
  let reads = 0
  await page.route('**/api/v1/agent/sources/**', r => r.fulfill(++reads === 1 ? { status: 409, json: { detail: '来源已变化，请重新提问获取最新引用' } } : { json: source }))
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('请解释'); await page.getByRole('button', { name: /发送$/ }).click()
  await page.getByRole('button', { name: /S1.*细胞生物学教材/ }).click()
  const drawer = page.getByRole('dialog')
  await expect(drawer).toContainText('来源已变化')
  await expect(drawer).not.toContainText(source.text)
  await drawer.getByRole('button', { name: '重新读取来源' }).click()
  await expect(drawer).toContainText(source.text)
  expect(reads).toBe(2)
})
test('Agent failure retains execution record and allows a new attempt', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/agent/answer', r => r.fulfill(++calls === 1 ? { status: 502, json: { detail: { code: 'UNVERIFIED_SOURCES', steps: steps.slice(0, 3) } } } : { json: result }))
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  const input = page.getByRole('textbox', { name: '输入学习问题' })
  await input.fill('解释呼吸'); await input.press('Enter')
  await expect(page.getByText('没有获得可核验的资料引用，请调整问题后重试。', { exact: true }).first()).toBeVisible()
  await page.getByText('执行记录', { exact: true }).click()
  await expect(page.getByText('资料检索完成', { exact: true })).toBeVisible()
  await expect(input).toBeEnabled(); await input.fill('用教材解释呼吸'); await input.press('Enter')
  await expect(page.locator('.chat-markdown')).toHaveText(result.message)
  expect(calls).toBe(2)
})
test('stopping native request aborts it and a subsequent request can complete', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    Object.assign(window, { agentCalls: 0, agentAborts: 0 })
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/agent/answer')) {
        const state = window as unknown as { agentCalls: number; agentAborts: number }
        if (++state.agentCalls === 1) return new Promise<Response>((_resolve, reject) => {
          init!.signal!.addEventListener('abort', () => { state.agentAborts++; reject(new DOMException('Stopped', 'AbortError')) }, { once: true })
        })
      }
      return original(input, init)
    }
  })
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  const input = page.getByRole('textbox', { name: '输入学习问题' })
  await input.fill('第一个问题'); await input.press('Enter')
  await expect(page.getByText('正在检索与核对资料…', { exact: true })).toBeVisible()
  await expect(page.getByRole('switch', { name: '只读资料 Agent' })).toBeDisabled()
  await page.getByRole('button', { name: '停止回复', exact: true }).click()
  await expect(page.getByText('已停止资料任务', { exact: true })).toBeVisible()
  await expect(input).toBeFocused()
  expect(await page.evaluate(() => (window as unknown as { agentAborts: number }).agentAborts)).toBe(1)
  await input.fill('下一个问题'); await input.press('Enter')
  await expect(page.locator('.chat-markdown')).toHaveText(result.message)
  expect(await page.evaluate(() => (window as unknown as { agentCalls: number }).agentCalls)).toBe(2)
})
test('incomplete Agent receipt is rejected rather than shown as a completed answer', async ({ page }) => {
  await page.route('**/api/v1/agent/answer', r => r.fulfill({ json: { ...result, steps: steps.slice(0, 3) } }))
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('解释'); await page.getByRole('button', { name: /发送$/ }).click()
  await expect(page.getByText('资料任务结果不完整，请重新提问。', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.chat-markdown')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /S1.*细胞生物学教材/ })).toHaveCount(0)
})
test('Agent history uses its actual tutor preset after selecting an ordinary chat role', async ({ page }) => {
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { tutor: { name: '学习辅导' }, mentor: { name: '研究导师' } } }))
  await page.goto('/chat')
  const role = page.getByRole('combobox', { name: '助教角色' })
  await expect(role).toBeEnabled(); await role.focus(); await role.press('ArrowDown')
  await expect(role).toHaveAttribute('aria-expanded', 'true')
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('研究导师', { exact: true }).click()
  await expect(page.locator('.chat-header .ant-select-selection-item')).toHaveText('研究导师')
  await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('根据资料解释'); await page.getByRole('button', { name: /发送$/ }).click()
  await expect(page.getByRole('button', { name: /根据资料解释.*学习辅导/ })).toBeVisible()
})
test('renamed source metadata is not shown as the original citation', async ({ page }) => {
  await page.route('**/api/v1/agent/sources/**', r => r.fulfill({ json: { ...source, filename: '已变更的教材.pdf', page_range: '20' } }))
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('解释'); await page.getByRole('button', { name: /发送$/ }).click()
  await page.getByRole('button', { name: /S1.*细胞生物学教材/ }).click()
  await expect(page.getByRole('dialog')).toContainText('来源版本无法核对，请重新提问。')
  await expect(page.getByRole('dialog')).not.toContainText(source.text)
})
for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }]) for (const theme of ['light', 'dark']) {
  test(`source reading and keyboard in ${viewport.width}px ${theme}`, async ({ page }) => {
    await page.addInitScript(() => {
      Object.assign(window, { focusTrace: [] as string[] })
      for (const type of ['focusin', 'focusout']) document.addEventListener(type, event => {
        const target = event.target as HTMLElement
        ;(window as unknown as { focusTrace: string[] }).focusTrace.push(`${type}: ${target.tagName}.${target.className}`)
      })
    })
    await page.setViewportSize(viewport)
    await page.addInitScript(t => localStorage.setItem('knowall-theme', t), theme)
    await page.goto('/chat')
    // The actual UI theme button is covered separately; set the theme through its persisted native key.
    await page.getByRole('switch', { name: '只读资料 Agent' }).click()
    await page.getByRole('textbox', { name: '输入学习问题' }).fill('从教材解释呼吸作用'); await page.getByRole('button', { name: /发送$/ }).click()
    const card = page.getByRole('button', { name: /S1.*细胞生物学教材/ })
    await card.focus(); await page.keyboard.press('Enter')
    await expect(page.getByRole('dialog')).toContainText(source.text)
    await expect(page.locator('.ant-drawer-content')).toHaveCSS('background-color', theme === 'dark' ? 'rgb(20, 38, 54)' : 'rgb(255, 255, 255)')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width)
    const dir = resolve('../docs/open-source-integration/native-agent-chat-2026-10-11/screenshots')
    await mkdir(dir, { recursive: true })
    await page.screenshot({ path: resolve(dir, `${process.env.AGENT_UI_PHASE || 'dev'}-${viewport.width}-${theme}-source.png`), fullPage: true, animations: 'disabled' })
    await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).not.toBeVisible()
    try { await expect(card).toBeFocused() } finally {
      console.log('citation focus trace', await page.evaluate(() => ({ active: document.activeElement?.outerHTML?.slice(0, 450), hasFocus: document.hasFocus(), trace: (window as unknown as { focusTrace: string[] }).focusTrace })))
    }
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.screenshot({ path: resolve(dir, `${process.env.AGENT_UI_PHASE || 'dev'}-${viewport.width}-${theme}-chat.png`), fullPage: true, animations: 'disabled' })
  })
}
