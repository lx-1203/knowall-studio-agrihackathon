import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
const saved = { run_id: 'input-run', conversation_id: null, assistant_message_id: null, status: 'failed', scope: 'readonly_documents', model: 'actual/model', error_code: 'MODEL_REQUEST_FAILED', sources: [], steps: [{ seq: 1, type: 'run_started' }, { seq: 2, type: 'run_failed' }], execution_mode: 'background', cancel_requested: false, recovery_state: 'terminal', input_saved: true }
const checkpoint = { run_id: 'input-run', message: '解释细胞呼吸，并核对教材中的出处。', requested_model: 'requested/model', created_at: '2026-10-11T16:13:24Z', version: 1 }
async function setup(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { tutor: { name: '学习助教' } } }))
  await page.addInitScript(() => localStorage.setItem('knowall_agent_observed_run:e2e-user', JSON.stringify({ run_id: 'input-run' })))
  await page.route('**/api/v1/agent/runs/input-run', r => r.fulfill({ json: saved }))
  await page.route('**/api/v1/agent/runs/input-run/input', r => r.fulfill({ json: checkpoint }))
}
test('retrieval fills only empty draft without replaying a model or changing selected model', async ({ page }) => {
  await setup(page); let posts = 0
  page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/api/v1/agent/')) posts++ })
  await page.goto('/chat'); await page.getByRole('button', { name: '取回任务输入' }).click()
  const dialog = page.getByRole('dialog', { name: '已保存的任务输入' })
  await expect(dialog).toContainText(checkpoint.message); await expect(dialog).toContainText('requested/model')
  await dialog.getByRole('button', { name: '放入输入框' }).click()
  await expect(page.locator('.chat-composer textarea')).toHaveValue(checkpoint.message)
  await expect(page.locator('.chat-composer textarea')).toBeFocused(); expect(posts).toBe(0)
})
test('existing draft remains untouched and deletion failure can retry without deleting result', async ({ page }) => {
  await setup(page); let deletes = 0; let removed = false; let posts = 0
  page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/api/v1/agent/')) posts++ })
  await page.route('**/api/v1/agent/runs/input-run', r => r.fulfill({ json: { ...saved, input_saved: !removed } }))
  await page.route('**/api/v1/agent/runs/input-run/input', r => {
    if (r.request().method() !== 'DELETE') return r.fulfill({ json: checkpoint })
    deletes++; if (deletes === 1) return r.fulfill({ status: 503, json: { detail: '删除暂时失败' } })
    removed = true; return r.fulfill({ status: 204 })
  })
  await page.goto('/chat'); await page.locator('.chat-composer textarea').fill('当前不能覆盖的草稿')
  await page.getByRole('button', { name: '取回任务输入' }).click()
  const dialog = page.getByRole('dialog', { name: '已保存的任务输入' })
  await expect(dialog.getByRole('button', { name: '放入输入框' })).toBeDisabled()
  await dialog.getByRole('button', { name: '删除已保存输入' }).click()
  await expect(dialog).toContainText('删除暂时失败'); await expect(dialog).toContainText(checkpoint.message)
  await dialog.getByRole('button', { name: '删除已保存输入' }).click()
  await expect(dialog).toBeHidden(); await expect(page.getByRole('button', { name: '取回任务输入' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重新读取运行状态' })).toBeFocused()
  await expect(page.locator('.chat-composer textarea')).toHaveValue('当前不能覆盖的草稿')
  await expect(page.getByText('已保存的任务详情', { exact: true })).toBeVisible(); expect(deletes).toBe(2); expect(posts).toBe(0)
})
test('failed retrieval clears plaintext and retry restores keyboard close focus', async ({ page }) => {
  await setup(page); let reads = 0
  await page.route('**/api/v1/agent/runs/input-run/input', r => r.fulfill(++reads === 1 ? { status: 503, json: { detail: '输入暂时无法读取' } } : { json: checkpoint }))
  await page.goto('/chat'); const trigger = page.getByRole('button', { name: '取回任务输入' }); await trigger.click()
  const dialog = page.getByRole('dialog', { name: '已保存的任务输入' })
  await expect(dialog).toContainText('输入暂时无法读取'); await expect(dialog.getByText(checkpoint.message)).toHaveCount(0)
  await dialog.getByRole('button', { name: '重新读取输入' }).click(); await expect(dialog).toContainText(checkpoint.message)
  await page.keyboard.press('Escape'); await expect(dialog).toBeHidden(); await expect(trigger).toBeFocused()
})
test('wrong-run input never exposes its plaintext', async ({ page }) => {
  await setup(page)
  await page.route('**/api/v1/agent/runs/input-run/input', r => r.fulfill({ json: { ...checkpoint, run_id: 'someone-else', message: '不得展示的正文' } }))
  await page.goto('/chat'); await page.getByRole('button', { name: '取回任务输入' }).click()
  await expect(page.getByRole('dialog')).toContainText('保存输入格式异常')
  await expect(page.getByText('不得展示的正文', { exact: true })).toHaveCount(0)
})
for (const width of [1440, 375]) for (const theme of ['light', 'dark']) test(`saved input ${width} ${theme} fits viewport and keyboard draft recovery`, async ({ page }) => {
  await setup(page); await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 })
  await page.goto('/chat'); if (theme === 'dark') await page.getByRole('button', { name: '切换到深色模式' }).click()
  const trigger = page.getByRole('button', { name: '取回任务输入' }); await trigger.focus(); await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: '已保存的任务输入' }); await expect(dialog).toContainText(checkpoint.message)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  const out = resolve('../docs/open-source-integration/native-agent-input-checkpoint-2026-10-12/screenshots'); await mkdir(out, { recursive: true })
  await page.screenshot({ path: resolve(out, `input-${width}-${theme}.png`), fullPage: true, animations: 'disabled' })
  await dialog.getByRole('button', { name: '放入输入框' }).focus(); await page.keyboard.press('Enter')
  await expect(page.locator('.chat-composer textarea')).toHaveValue(checkpoint.message)
  await expect(page.locator('.chat-composer textarea')).toBeFocused()
})
test('save input option is explicit and absent until backend advertises readiness', async ({ page }) => {
  let ready = false; let body: Record<string, unknown> | null = null
  await page.route('**/api/v1/agent/models', r => r.fulfill({ json: { models: [{ value: 'local', label: '本地模型', model_tool_support: 'unverified' }], background_runs: true, input_checkpoints: ready } }))
  await page.route('**/api/v1/agent/runs', r => { body = r.request().postDataJSON(); return r.fulfill({ status: 503, json: { detail: '模拟提交失败' } }) })
  await page.goto('/chat'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  await page.getByRole('switch', { name: '离开页面后继续' }).click()
  await expect(page.getByRole('switch', { name: '加密保存任务输入' })).toHaveCount(0)
  ready = true; await page.getByRole('switch', { name: '只读资料 Agent' }).click(); await expect(page.getByRole('switch', { name: '只读资料 Agent' })).toHaveAttribute('aria-checked', 'false'); await page.getByRole('switch', { name: '只读资料 Agent' }).click()
  const save = page.getByRole('switch', { name: '加密保存任务输入' }); await expect(save).toHaveAttribute('aria-checked', 'false'); await save.click()
  await page.locator('.chat-composer textarea').fill('测试明确保存'); await page.getByRole('button', { name: '发送消息' }).click()
  await expect.poll(() => body?.persist_input).toBe(true)
})
