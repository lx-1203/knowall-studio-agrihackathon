import { test, expect } from './fixtures'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import type { Page } from '@playwright/test'

const histories = [
  { id: 'biology', title: '细胞呼吸与能量转化', role_preset: 'tutor', created_at: '2026-10-10T19:30:00Z' },
  { id: 'english', title: '英语长难句分析', role_preset: 'tutor', created_at: '2026-10-09T10:00:00Z' },
]
const answer = '## 先理解，再记忆\n\n细胞通过呼吸作用释放能量。\n\n| 阶段 | 位置 |\n| --- | --- |\n| 糖酵解 | 细胞质 |\n| 三羧酸循环 | 线粒体 |\n\n```text\n' + '很长的教材引用'.repeat(24) + '\n```'
async function capture(page: Page, name: string) {
  const dir = resolve('../docs/open-source-integration/ui-chat-2026-10-11/screenshots')
  await mkdir(dir, { recursive: true })
  await page.screenshot({ path: resolve(dir, `${process.env.CHAT_UI_PHASE || 'current'}-${name}.png`), fullPage: true, animations: 'disabled' })
}
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/chat/conversations', r => r.fulfill({ json: histories }))
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { tutor: { name: '学习辅导' }, mentor: { name: '研究导师' } } }))
  await page.route('**/api/v1/chat/conversations/*', r => r.fulfill({ json: {
    conversation_id: r.request().url().split('/').pop(), title: histories.find(h => r.request().url().endsWith(h.id))?.title,
    role_preset: 'tutor', messages: [
      { id: 'u', role: 'user', content: '请帮我理解细胞呼吸，并整理关键概念。', created_at: '' },
      { id: 'a', role: 'assistant', content: answer, created_at: '' },
    ],
  } }))
})
test('history search filters locally and keyboard selects a conversation', async ({ page }) => {
  let reads = 0
  page.on('request', r => { if (r.url().endsWith('/chat/conversations')) reads++ })
  await page.goto('/chat')
  await expect(page.getByText('细胞呼吸与能量转化', { exact: true })).toBeVisible()
  await capture(page, 'desktop-empty')
  await page.getByRole('textbox', { name: '搜索历史对话' }).fill('英语')
  await expect(page.getByRole('button', { name: /细胞呼吸与能量转化/ })).toHaveCount(0)
  const history = page.getByRole('button', { name: /英语长难句分析/ })
  await history.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('.chat-markdown')).toContainText('细胞通过呼吸作用')
  await page.getByRole('textbox', { name: '搜索历史对话' }).fill('不存在')
  await expect(page.getByText('没有匹配的对话', { exact: true })).toBeVisible()
  expect(reads).toBe(1)
})
test('mobile history drawer closes after selection without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/chat')
  await expect(page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...')).toBeVisible()
  await capture(page, 'mobile-empty')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
  await page.getByRole('button', { name: '打开对话历史', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '对话历史' })
  await expect(drawer).toBeVisible()
  await drawer.getByRole('textbox', { name: '搜索历史对话' }).fill('细胞')
  await drawer.getByRole('button', { name: /细胞呼吸与能量转化/ }).click()
  await expect(drawer).not.toBeVisible()
  await expect(page.locator('.chat-markdown')).toContainText('细胞通过呼吸作用')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
  await capture(page, 'mobile-conversation')
})
test('IME Enter does not send and Shift Enter still inserts a newline', async ({ page }) => {
  let writes = 0
  await page.route('**/api/v1/chat/assistant/stream', r => {
    writes++; return r.fulfill({ contentType: 'text/event-stream', body: 'data:{"token":"中文回答"}\n\ndata:{"done":true,"conversation_id":"chinese"}\n\n' })
  })
  await page.goto('/chat')
  const input = page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...')
  await input.fill('中文输入')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true, keyCode: 229 })
  await expect(input).toHaveValue('中文输入')
  expect(writes).toBe(0)
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('中文输入\n')
  await input.press('Enter')
  await expect(page.locator('.chat-markdown')).toHaveText('中文回答')
  expect(writes).toBe(1)
})
test('stop cancels the actual stream, retains text and allows another message', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    Object.assign(window, { chatCalls: 0, chatStops: 0 })
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/chat/assistant/stream')) {
        const state = window as unknown as { chatCalls: number; chatStops: number }
        state.chatCalls++
        return new Response(new ReadableStream({
          start(c) { c.enqueue(new TextEncoder().encode(state.chatCalls === 1 ? 'data:{"token":"已生成的一段"}\n\n' : 'data:{"token":"下一次完成"}\n\ndata:{"done":true,"conversation_id":"next"}\n\n')) },
          cancel() { state.chatStops++ },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return original(input, init)
    }
  })
  await page.goto('/chat')
  const input = page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...')
  await input.fill('一个较长问题'); await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown')).toHaveText('已生成的一段')
  await page.getByRole('button', { name: '停止回复', exact: true }).click()
  await expect(page.getByText('已停止回复', { exact: true })).toBeVisible()
  await expect(input).toBeEnabled()
  await expect(input).toBeFocused()
  await expect.poll(() => page.evaluate(() => (window as unknown as { chatStops: number }).chatStops)).toBe(1)
  await input.fill('继续'); await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown').last()).toHaveText('下一次完成')
  await expect(page.locator('.chat-markdown').first()).toHaveText('已生成的一段')
  expect(await page.evaluate(() => (window as unknown as { chatCalls: number }).chatCalls)).toBe(2)
})
test('suggested question fills and focuses the composer without generating', async ({ page }) => {
  let writes = 0
  await page.route('**/api/v1/chat/assistant**', r => { writes++; return r.fulfill({ status: 500 }) })
  await page.goto('/chat')
  await page.getByRole('button', { name: /梳理一个知识点/ }).click()
  const input = page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...')
  await expect(input).toHaveValue('请用通俗的语言解释这个知识点，并给出一个具体例子：')
  await expect(input).toBeFocused()
  expect(writes).toBe(0)
})
for (const mode of ['light', 'dark']) test(`long messages, tables and code fit desktop and theme ${mode}`, async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem('knowall-theme', value), mode)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/chat'); await page.getByText('细胞呼吸与能量转化', { exact: true }).click()
  await expect(page.locator('.chat-markdown')).toContainText('细胞通过呼吸作用')
  const composer = await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').boundingBox()
  expect(composer!.y + composer!.height).toBeLessThanOrEqual(1000)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440)
  const paragraph = page.locator('.chat-markdown p').first()
  const contrast = await paragraph.evaluate(el => {
    const rgb = (s: string) => s.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(n => { const x = n / 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4 })
    const luminance = (s: string) => { const [r, g, b] = rgb(s); return .2126 * r + .7152 * g + .0722 * b }
    let node: Element | null = el, bg = ''
    while (node) { bg = getComputedStyle(node).backgroundColor; if (!bg.endsWith(', 0)') && bg !== 'transparent') break; node = node.parentElement }
    const fgL = luminance(getComputedStyle(el).color), bgL = luminance(bg)
    return (Math.max(fgL, bgL) + .05) / (Math.min(fgL, bgL) + .05)
  })
  expect(contrast).toBeGreaterThanOrEqual(4.5)
  await capture(page, `desktop-${mode}-conversation`)
})
test('copy reply uses the original markdown content', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (text: string) => { Object.assign(window, { copiedReply: text }) } } }))
  await page.goto('/chat'); await page.getByText('细胞呼吸与能量转化', { exact: true }).click()
  await page.getByRole('button', { name: '复制回复', exact: true }).click()
  expect(await page.evaluate(() => (window as unknown as { copiedReply: string }).copiedReply)).toBe(answer)
})
test('deep theme uses the same product background in the shell and sidebar', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('knowall-theme', 'dark'))
  await page.goto('/chat')
  await expect(page.getByRole('heading', { name: 'AI 学习助手', exact: true })).toBeVisible()
  await expect(page.locator('.product-content')).toHaveCSS('background-color', 'rgb(14, 29, 42)')
  await expect(page.locator('.product-sidebar')).toHaveCSS('background-color', 'rgb(20, 38, 54)')
})
test('resizing an open history drawer restores exactly one desktop history list', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/chat'); await page.getByRole('button', { name: '打开对话历史' }).click()
  await expect(page.getByRole('dialog', { name: '对话历史' })).toBeVisible()
  await page.setViewportSize({ width: 1280, height: 900 })
  await expect(page.getByRole('dialog', { name: '对话历史' })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: '搜索历史对话' })).toHaveCount(1)
  await page.getByRole('button', { name: /细胞呼吸与能量转化/ }).click()
  await expect(page.locator('.chat-markdown')).toContainText('细胞通过呼吸作用')
})
test('copy denial preserves the reply and offers a readable recovery message', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('denied') } } }))
  await page.goto('/chat'); await page.getByText('细胞呼吸与能量转化', { exact: true }).click()
  await page.getByRole('button', { name: '复制回复', exact: true }).click()
  await expect(page.getByText('复制失败，请手动选择文字复制', { exact: true })).toBeVisible()
  await expect(page.locator('.chat-markdown')).toContainText('细胞通过呼吸作用')
})
test('reading earlier messages pauses automatic scrolling until returning to latest', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/chat/assistant/stream')) return new Response(new ReadableStream({ start(c) {
        Object.assign(window, { emitChatChunk: () => c.enqueue(new TextEncoder().encode('data:{"token":"\\n\\n新的一段回复"}\n\n')) })
        c.enqueue(new TextEncoder().encode(`data:${JSON.stringify({ token: '正在解释的概念。\n\n'.repeat(45) })}\n\n`))
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
      return original(input, init)
    }
  })
  await page.goto('/chat')
  await page.getByRole('textbox', { name: '输入学习问题' }).fill('解释一个长概念')
  await page.getByRole('button', { name: /发送/ }).click()
  const log = page.getByRole('log', { name: '对话消息' })
  await expect.poll(() => log.evaluate(el => el.scrollTop)).toBeGreaterThan(50)
  await log.evaluate(el => { el.scrollTop = 0 })
  await expect(page.getByRole('button', { name: '回到最新', exact: true })).toBeVisible()
  await page.evaluate(() => (window as unknown as { emitChatChunk: () => void }).emitChatChunk())
  await expect(page.locator('.chat-markdown')).toContainText('新的一段回复')
  expect(await log.evaluate(el => el.scrollTop)).toBeLessThan(5)
  await page.getByRole('button', { name: '回到最新', exact: true }).click()
  await expect.poll(() => log.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(5)
  await expect(page.getByRole('button', { name: '回到最新', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '停止回复', exact: true }).click()
})
test('empty desktop starts at the welcome heading and mobile keeps the reading width', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto('/chat')
  const log = page.getByRole('log', { name: '对话消息' })
  const welcome = page.getByRole('heading', { name: '今天想弄懂什么？', exact: true })
  await expect(welcome).toBeInViewport()
  expect(await log.evaluate(el => el.scrollTop)).toBe(0)
  await page.setViewportSize({ width: 375, height: 812 })
  await expect(page.getByRole('button', { name: '打开对话历史' })).toBeVisible()
  expect((await page.locator('.chat-main').boundingBox())!.width).toBeGreaterThanOrEqual(340)
})
test('late stream cleanup from a stopped reply never selects its old conversation', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    let calls = 0
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/chat/assistant/stream')) {
        const first = ++calls === 1
        return new Response(new ReadableStream({
          start(c) { c.enqueue(new TextEncoder().encode(`data:${JSON.stringify({ token: first ? '旧回复已经到达' : '新对话回复' })}\n\ndata:${JSON.stringify({ done: true, conversation_id: first ? 'old-conversation' : 'new-conversation' })}\n\n`)) },
          cancel() { if (first) return new Promise<void>(resolve => { Object.assign(window, { releaseOldCleanup: resolve }) }) },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return original(input, init)
    }
  })
  await page.goto('/chat')
  const input = page.getByRole('textbox', { name: '输入学习问题' })
  await input.fill('旧问题'); await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown')).toHaveText('旧回复已经到达')
  await page.getByRole('button', { name: '停止回复', exact: true }).click()
  await page.getByRole('button', { name: /新对话/ }).click()
  await input.fill('新的问题'); await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.getByRole('heading', { name: '新的问题', exact: true })).toBeVisible()
  await page.evaluate(async () => { (window as unknown as { releaseOldCleanup: () => void }).releaseOldCleanup(); await new Promise<void>(resolve => setTimeout(resolve, 0)) })
  await expect(page.getByRole('heading', { name: '新的问题', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /旧问题/ })).toHaveCount(0)
  await expect(page.locator('.chat-markdown')).toHaveText('新对话回复')
})
test('clipboard error feedback inherits the active dark theme', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('knowall-theme', 'dark')
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('denied') } } })
  })
  await page.goto('/chat'); await page.getByText('细胞呼吸与能量转化', { exact: true }).click()
  await page.getByRole('button', { name: '复制回复', exact: true }).click()
  await expect(page.getByText('复制失败，请手动选择文字复制', { exact: true })).toBeVisible()
  const background = await page.locator('.ant-message-notice-content').evaluate(el => getComputedStyle(el).backgroundColor.match(/\d+/g)!.slice(0, 3).map(Number))
  expect(Math.max(...background)).toBeLessThan(80)
})
