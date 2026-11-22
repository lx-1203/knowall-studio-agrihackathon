import { test, expect, E2E_TOKEN } from './fixtures'

const article = { id: 1, title: '协议阅读', content: '中文能量阅读正文', difficulty: 1, tags: [] }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/chat/roles', r => r.fulfill({ json: { tutor: { name: '助教' } } }))
  await page.route('**/api/v1/reading/articles*', r => r.fulfill({ json: [article] }))
  await page.route('**/api/v1/reading/articles/1', r => r.fulfill({ json: article }))
})

for (const cached of [false, true]) {
  test(`reading accepts multiline CRLF and no-space data (${cached ? 'cached' : 'done'})`, async ({ page }) => {
    let fallbacks = 0
    await page.route('**/api/v1/reading/convert', r => { fallbacks++; return r.fulfill({ status: 400, json: { detail: 'unexpected replay' } }) })
    await page.route('**/api/v1/reading/convert/stream', r => {
      expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
      return r.fulfill({ contentType: 'text/event-stream', body: `: keepalive\r\nevent: message\r\ndata:{"${cached ? 'cached' : 'done'}":true,\r\ndata:"result":"协议正确 energy",\r\ndata:"vocabulary":[{"cn":"能量","en":"energy"}]}\r\n\r\ndata:{"done":true,"result":"重复","vocabulary":[]}\r\n\r\n` })
    })
    await page.goto('/reading')
    await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
    await expect(page.locator('.reading-text')).toContainText('协议正确')
    expect(fallbacks).toBe(0)
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
    expect(saved.totalRead).toBe(1)
    expect(saved.words.energy.seen).toBe(1)
  })
}

test('chat decodes Chinese byte slices and multiline events exactly once', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/chat/assistant/stream')) {
        const bytes = new TextEncoder().encode(': heartbeat\r\ndata:{"token":"中文",\r\ndata:"done":false}\r\n\r\ndata:{"token":"能量","done":false}\r\n\r\ndata:{"done":true,"conversation_id":"stream-conv"}\r\n\r\ndata:{"token":"不应显示"}\r\n\r\n')
        let index = 0
        return new Response(new ReadableStream({ pull(controller) { if (index < bytes.length) controller.enqueue(bytes.slice(index, ++index)); else controller.close() } }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return original(input, init)
    }
  })
  await page.goto('/chat')
  await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('解释')
  await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown')).toHaveText('中文能量')
  await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
})

for (const rag of [false, true]) {
  test(`chat non-streaming sends current bearer token (${rag ? 'rag' : 'normal'})`, async ({ page }) => {
    let calls = 0
    await page.route(`**/api/v1/chat/assistant${rag ? '/rag' : ''}`, r => {
      calls++
      expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
      return r.fulfill({ json: { message: '账号正文', conversation_id: 'account-conv' } })
    })
    await page.goto('/chat')
    if (rag) await page.getByRole('switch').nth(0).click()
    await page.getByRole('switch').nth(1).click()
    await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('账号请求')
    await page.getByRole('button', { name: /发送/ }).click()
    await expect(page.locator('.chat-markdown')).toHaveText('账号正文')
    expect(calls).toBe(1)
  })
}

for (const failure of ['error', 'eof', 'malformed', '401']) {
  test(`chat exposes ${failure} without replaying the generation`, async ({ page }) => {
    let replays = 0
    await page.route('**/api/v1/chat/assistant', r => { replays++; return r.fulfill({ json: { message: '意外重试' } }) })
    await page.route('**/api/v1/chat/assistant/stream', r => {
      expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
      if (failure === '401') return r.fulfill({ status: 401, json: { detail: 'expired' } })
      const tail = failure === 'error' ? 'data:{"error":"服务端停止","done":true}\n\n' : failure === 'malformed' ? 'data:broken-json\n\n' : ''
      return r.fulfill({ contentType: 'text/event-stream', body: 'data: {"token":"已生成片段"}\n\n' + tail })
    })
    await page.goto('/chat')
    await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('中断请求')
    await page.getByRole('button', { name: /发送/ }).click()
    await expect(page.locator('#root .ant-alert-message').filter({ hasText: failure === '401' ? '登录已失效，请重新登录' : '回复未完成' })).toBeVisible()
    if (failure !== '401') await expect(page.locator('.chat-markdown')).toContainText('已生成片段')
    await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
    // Wait across the old 500 ms automatic replay timer to verify its removal.
    await page.waitForTimeout(650)
    expect(replays).toBe(0)
  })
}

test('reading unauthorized stream does not start a second model request', async ({ page }) => {
  let fallbacks = 0
  await page.route('**/api/v1/reading/convert/stream', r => r.fulfill({ status: 401, json: { detail: 'expired' } }))
  await page.route('**/api/v1/reading/convert', r => { fallbacks++; return r.fulfill({ json: { result: '错误回退', vocabulary: [] } }) })
  await page.goto('/reading')
  await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
  await expect(page.getByText('登录已失效，请重新登录')).toBeVisible()
  expect(fallbacks).toBe(0)
  expect(await page.evaluate(() => localStorage.getItem('reading_lang_vocab'))).toBeNull()
})

test('navigating away cancels chat reader and never retries', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      if (String(input).endsWith('/chat/assistant/stream')) {
        return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"token":"等待中"}\n\n')) }, cancel() { (window as any).chatCancelled = true } }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (String(input).endsWith('/chat/assistant') && init?.method === 'POST') (window as any).chatReplayed = true
      return original(input, init)
    }
  })
  await page.goto('/chat')
  await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('等待问题')
  await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown')).toContainText('等待中')
  await page.getByRole('link', { name: '学习进度', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).chatCancelled)).toBe(true)
  expect(await page.evaluate(() => (window as any).chatReplayed)).toBeUndefined()
})

test('RAG streaming reaches its authenticated endpoint and stops at done', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/chat/assistant/rag/stream', r => {
    calls++
    expect(r.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    return r.fulfill({ contentType: 'text/event-stream', body: 'data:{"token":"资料回答"}\r\n\r\ndata:{"done":true,"conversation_id":"rag-stream"}\r\n\r\n' })
  })
  await page.goto('/chat')
  await page.getByRole('switch').nth(0).click()
  await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('资料提问')
  await page.getByRole('button', { name: /发送/ }).click()
  await expect(page.locator('.chat-markdown')).toHaveText('资料回答')
  await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
  expect(calls).toBe(1)
})

test('protocol adapter bounds memory, rejects invalid UTF-8/JSON and releases every reader', async ({ page }) => {
  await page.goto('/chat')
  const results = await page.evaluate(async () => {
    const { consumeJSONEventStream } = await import('/src/services/jsonEventStream.ts')
    const cases = [
      { name: 'invalid JSON', bytes: new TextEncoder().encode('data:[]\n\n') },
      { name: 'invalid UTF-8', bytes: new Uint8Array([100, 97, 116, 97, 58, 255, 10, 10]) },
      { name: 'buffer overflow', bytes: new TextEncoder().encode('data:' + 'x'.repeat(1024 * 1024 + 1)) },
      { name: 'bare CR', bytes: new TextEncoder().encode('data:{"done":true}\r\rdata:{"done":true}\r\r'), valid: true },
    ]
    const results = []
    for (const item of cases) {
      let cancelled = false, events = 0, error = ''
      const response = new Response(new ReadableStream({ start(c) { c.enqueue(item.bytes) }, cancel() { cancelled = true } }), { headers: { 'Content-Type': 'text/event-stream' } })
      try { await consumeJSONEventStream(response, () => { events++; return true }) } catch (e) { error = String(e) }
      results.push({ name: item.name, error: !!error, cancelled, locked: response.body!.locked, events, valid: item.valid || false })
    }
    return results
  })
  for (const result of results) {
    expect(result.error, result.name).toBe(!result.valid)
    expect(result.cancelled, result.name).toBe(true)
    expect(result.locked, result.name).toBe(false)
    expect(result.events, result.name).toBe(result.valid ? 1 : 0)
  }
})

