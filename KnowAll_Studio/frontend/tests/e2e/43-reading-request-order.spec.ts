import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

const articles = [
  { id: 1, title: '旧文章', content: '旧请求中文内容', difficulty: 1, tags: [] },
  { id: 2, title: '新文章', content: '新请求中文内容', difficulty: 1, tags: [] },
]
const terminal = (old: boolean) => ({ done: true, result: old ? '旧结果 oldword' : '新结果 newword', vocabulary: [{ cn: old ? '旧词' : '新词', en: old ? 'oldword' : 'newword' }] })
const saved = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
const pick = (page: Page, id: number) => page.locator('.reading-article-item').filter({ hasText: articles[id - 1].title }).click()

// Explicit in-browser simulation can deliberately ignore the transport abort.
// This proves identity guards independently of a cooperative network stack.
async function controlledFetch(page: Page, mode: 'late-success' | 'late-error' | 'live') {
  await page.addInitScript(({ mode, terminal }) => {
    const native = window.fetch.bind(window)
    const state = { arrived: [] as string[], aborted: [] as string[], released: [] as string[], gates: {} as Record<string, () => void> }
    ;(window as any).readingSimulation = state
    ;(window as any).releaseReading = (key: string) => { state.released.push(key); state.gates[key]?.() }
    window.fetch = async (input, init) => {
      if (!String(input).endsWith('/reading/convert/stream')) return native(input, init)
      const text = JSON.parse(String(init?.body)).text
      const key = text.includes('旧') ? 'old' : 'new'
      state.arrived.push(key)
      init?.signal?.addEventListener('abort', () => state.aborted.push(key))
      const old = key === 'old'
      const value = old ? { ...terminal, result: '旧结果 oldword', vocabulary: [{ cn: '旧词', en: 'oldword' }] } : terminal
      if (mode === 'live') {
        return new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('data: {"token":"明确模拟首片段"}\n\n'))
            state.gates[key] = () => { try { controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`)); controller.close() } catch { /* canceled reader */ } }
          },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      await new Promise<void>(resolve => { state.gates[key] = resolve })
      if (mode === 'late-error' && old) throw new Error('explicit late simulated failure')
      return new Response(`data: ${JSON.stringify(value)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
    }
  }, { mode, terminal: terminal(false) })
}
const arrived = (page: Page, key: string) => expect.poll(() => page.evaluate(key => (window as any).readingSimulation.arrived.includes(key), key)).toBe(true)
const release = (page: Page, key: string) => page.evaluate(key => (window as any).releaseReading(key), key)
// A task boundary after the completed promise/microtask chain; no padded waits.
const settle = (page: Page) => page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/reading/articles*', r => r.fulfill({ json: articles }))
  for (const a of articles) await page.route(`**/api/v1/reading/articles/${a.id}`, r => r.fulfill({ json: a }))
})

test('late article details cannot replace a newer completed selection', async ({ page }) => {
  let late: (() => Promise<void>) | undefined
  let oldConversions = 0
  await page.route('**/api/v1/reading/articles/1', r => { late = () => r.fulfill({ json: articles[0] }); return new Promise<void>(() => {}) })
  await page.route('**/api/v1/reading/convert/stream', r => {
    const old = r.request().postDataJSON().text.includes('旧')
    if (old) oldConversions++
    return r.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify(terminal(old))}\n\n` })
  })
  await page.goto('/reading'); await pick(page, 1)
  await expect.poll(() => !!late).toBe(true)
  await pick(page, 2); await expect(page.locator('.reading-text')).toContainText('新结果')
  await late!().catch(() => {})
  await settle(page)
  await expect(page.locator('.reading-main-header h2')).toContainText('新文章')
  await expect(page.locator('.reading-text')).toContainText('新结果')
  expect(oldConversions).toBe(0)
  expect((await saved(page)).totalRead).toBe(1)
})

test('late stream terminal cannot render or increment vocabulary after newer success', async ({ page }) => {
  await controlledFetch(page, 'late-success'); await page.goto('/reading')
  await pick(page, 1); await arrived(page, 'old')
  await pick(page, 2); await arrived(page, 'new'); await release(page, 'new')
  await expect(page.locator('.reading-text')).toContainText('新结果')
  await release(page, 'old'); await settle(page)
  await expect(page.locator('.reading-text')).toContainText('新结果')
  expect(await saved(page)).toMatchObject({ totalRead: 1, words: { newword: { seen: 1 } } })
  expect((await saved(page)).words.oldword).toBeUndefined()
})

test('late failure does not replay fallback or clear the newer loading operation', async ({ page }) => {
  let fallbacks = 0
  await page.route('**/api/v1/reading/convert', r => { fallbacks++; return r.fulfill({ json: terminal(true) }) })
  await controlledFetch(page, 'late-error'); await page.goto('/reading')
  await pick(page, 1); await arrived(page, 'old')
  await pick(page, 2); await arrived(page, 'new'); await release(page, 'old'); await settle(page)
  expect(fallbacks).toBe(0)
  await expect(page.getByRole('button', { name: /换一批词/ })).toBeDisabled()
  await expect(page.getByText('⏳ 正在生成...')).toBeVisible()
  await release(page, 'new'); await expect(page.locator('.reading-text')).toContainText('新结果')
  expect((await saved(page)).totalRead).toBe(1)
})

test('pending nonstream fallback is canceled and cannot overwrite newer success', async ({ page }) => {
  let late: (() => Promise<void>) | undefined
  let canceled = false
  page.on('requestfailed', r => { if (r.url().endsWith('/reading/convert')) canceled = true })
  await page.route('**/api/v1/reading/convert/stream', r => r.fulfill({ status: 503, json: { detail: 'explicit simulation' } }))
  await page.route('**/api/v1/reading/convert', r => {
    if (r.request().postDataJSON().text.includes('旧')) { late = () => r.fulfill({ json: terminal(true) }); return new Promise<void>(() => {}) }
    return r.fulfill({ json: terminal(false) })
  })
  await page.goto('/reading'); await pick(page, 1); await expect.poll(() => !!late).toBe(true)
  await pick(page, 2); await expect(page.locator('.reading-text')).toContainText('新结果')
  await late!().catch(() => {}); await settle(page)
  await expect(page.locator('.reading-text')).toContainText('新结果')
  expect((await saved(page)).totalRead).toBe(1)
  await expect.poll(() => canceled).toBe(true)
})

test('custom conversion superseded by article cancels live reader without replay', async ({ page }) => {
  let fallbacks = 0
  await page.route('**/api/v1/reading/convert', r => { fallbacks++; return r.fulfill({ json: terminal(true) }) })
  await controlledFetch(page, 'live'); await page.goto('/reading')
  await page.getByRole('button', { name: /自定义/ }).click()
  await page.getByPlaceholder(/在此粘贴中文文本/).fill('旧自定义中文')
  await page.getByRole('button', { name: /开始阅读/ }).click(); await arrived(page, 'old')
  await page.getByRole('button', { name: /文章库/ }).click()
  await pick(page, 2); await arrived(page, 'new'); await release(page, 'new')
  await expect(page.locator('.reading-text')).toContainText('新结果')
  await release(page, 'old'); await settle(page)
  expect(fallbacks).toBe(0)
  expect(await page.evaluate(() => (window as any).readingSimulation.aborted)).toContain('old')
  expect((await saved(page)).totalRead).toBe(1)
})

test('unmount aborts live reader and cannot persist a late result', async ({ page }) => {
  let fallbacks = 0
  await page.route('**/api/v1/reading/convert', r => { fallbacks++; return r.fulfill({ json: terminal(true) }) })
  await controlledFetch(page, 'live'); await page.goto('/reading')
  await pick(page, 1); await arrived(page, 'old')
  await page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.locator('.reading-main')).toHaveCount(0)
  await release(page, 'old'); await settle(page)
  expect(await page.evaluate(() => (window as any).readingSimulation.aborted)).toContain('old')
  expect(fallbacks).toBe(0)
  expect(await saved(page)).toEqual({})
})
