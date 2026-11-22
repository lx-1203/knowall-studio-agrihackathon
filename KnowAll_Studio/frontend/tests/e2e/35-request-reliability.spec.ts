import { test, expect, E2E_TOKEN } from './fixtures'
import { summaryData } from './summary-navigation-fixture'

const switchTo = async (page: any, id: string) => page.evaluate((id: string) => {
  history.pushState(null, '', `/summary/${id}`); dispatchEvent(new PopStateEvent('popstate'))
}, id)
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
const plan = (name: string) => ({ results: { study_plan: { status: 'success', result: { name, plan_id: name, ebbinghaus_nodes: [] } } } })
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/knowledge/summary/*', r => {
    const id = r.request().url().split('/').pop()
    return r.fulfill({ json: summaryData(id, id === 'b' ? '# 新资料B' : '# 旧资料A') })
  })
  await page.route('**/api/v1/knowledge/coverage/*', r => r.fulfill({ json: null }))
  await page.route('**/api/v1/memory/stats', r => r.fulfill({ json: {} }))
})

test('late summary A cannot replace B after a route switch', async ({ page }) => {
  let started = false
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/knowledge/summary/a', async r => {
    started = true; await gate; await r.fulfill({ json: summaryData('a', '# 旧资料A') }).catch(() => {})
  })
  await page.goto('/summary/a'); await expect.poll(() => started).toBe(true)
  await switchTo(page, 'b'); await expect(page.locator('.markdown-body')).toContainText('新资料B')
  release(); await pause(300); await expect(page.locator('.markdown-body')).toContainText('新资料B')
  await expect(page.getByText('加载知识总纲失败', { exact: false })).toBeHidden()
})

test('auxiliary coverage does not block a ready summary', async ({ page }) => {
  let started = false
  await page.route('**/api/v1/knowledge/coverage/a', async r => {
    started = true; await pause(3000); await r.fulfill({ json: null }).catch(() => {})
  })
  await page.goto('/summary/a'); await expect.poll(() => started).toBe(true)
  await expect(page.locator('.markdown-body')).toContainText('旧资料A', { timeout: 800 })
})

test('generation result from A cannot populate B or announce success', async ({ page }) => {
  let started = false
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/agents/orchestrate', async r => {
    started = true; await gate; await r.fulfill({ json: plan('旧资料A计划') }).catch(() => {})
  })
  await page.goto('/summary/a'); await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await expect.poll(() => started).toBe(true); await switchTo(page, 'b')
  await expect(page.locator('.markdown-body')).toContainText('新资料B')
  release(); await pause(300); await page.getByRole('tab', { name: /学习计划$/ }).click()
  await expect(page.getByText('旧资料A计划', { exact: true })).toBeHidden()
  await expect(page.getByText('尚未生成学习计划', { exact: true })).toBeVisible()
  await expect(page.getByText(/配套内容生成完成/)).toBeHidden()
})

test('completed derived state is cleared when changing summaries', async ({ page }) => {
  await page.route('**/api/v1/agents/orchestrate', r => r.fulfill({ json: plan('A已完成计划') }))
  await page.goto('/summary/a'); await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await expect(page.getByText(/配套内容生成完成/)).toBeVisible()
  await switchTo(page, 'b'); await expect(page.locator('.markdown-body')).toContainText('新资料B')
  await page.getByRole('tab', { name: /学习计划$/ }).click()
  await expect(page.getByText('A已完成计划', { exact: true })).toBeHidden()
  await expect(page.getByText('尚未生成学习计划', { exact: true })).toBeVisible()
})

// Import the actual app API in the Vite page; routes below count real Axios HTTP sends.
async function callApi(page: any, method: string, path: string, cancelAfter?: number, timeout?: number) {
  return page.evaluate(async ({ method, path, cancelAfter, timeout }: any) => {
    const { default: api } = await import(/* @vite-ignore */ '/src/api.ts')
    const controller = new AbortController(); const started = performance.now()
    if (cancelAfter !== undefined) setTimeout(() => controller.abort(), cancelAfter)
    try {
      const r = await api.request({ method, url: path, ...(timeout === undefined ? {} : { timeout }), data: method === 'post' ? { summary_id: 'a', config: { question_count: 30 } } : undefined, signal: controller.signal })
      return { ok: true, data: r.data, elapsed: performance.now() - started }
    } catch (e: any) { return { ok: false, code: e.code, status: e.response?.status, elapsed: performance.now() - started } }
  }, { method, path, cancelAfter, timeout })
}

test('generations and all write methods are sent once on 503', async ({ page }) => {
  await page.goto('/summary/b')
  for (const method of ['post', 'put', 'patch', 'delete']) {
    let count = 0
    await page.route('**/api/v1/agents/retry-proof', r => { count++; return r.fulfill({ status: 503, json: { detail: 'uncertain write' } }) })
    const result = await callApi(page, method, '/agents/retry-proof')
    expect(result.ok).toBe(false); expect(count, method).toBe(1)
    await page.unroute('**/api/v1/agents/retry-proof')
  }
})

test('transient GET recovers with preserved Bearer and bounded attempts', async ({ page }) => {
  let count = 0; const headers: string[] = []
  await page.route('**/api/v1/reliability-read', r => {
    count++; headers.push(r.request().headers().authorization)
    return r.fulfill({ status: count < 3 ? 503 : 200, json: { recovered: true } })
  })
  await page.goto('/summary/b'); const result = await callApi(page, 'get', '/reliability-read')
  expect(result.ok).toBe(true); expect(count).toBe(3); expect(headers).toEqual(Array(3).fill(`Bearer ${E2E_TOKEN}`))
})

test('exhausted GET stops at three attempts', async ({ page }) => {
  let count = 0
  await page.route('**/api/v1/reliability-exhausted', r => { count++; return r.fulfill({ status: 503, json: {} }) })
  await page.goto('/summary/b'); const result = await callApi(page, 'get', '/reliability-exhausted')
  expect(result.ok).toBe(false); expect(result.status).toBe(503); expect(count).toBe(3)
})

test('abort during retry delay rejects promptly without another send', async ({ page }) => {
  let count = 0
  await page.route('**/api/v1/reliability-cancel', r => { count++; return r.fulfill({ status: 503, json: {} }) })
  await page.goto('/summary/b'); const result = await callApi(page, 'get', '/reliability-cancel', 120)
  expect(result.ok).toBe(false); expect(result.code).toBe('ERR_CANCELED'); expect(result.elapsed).toBeLessThan(700)
  await pause(1200); expect(count).toBe(1)
})

test('401 is not retried and retains logout behavior', async ({ page }) => {
  let count = 0
  await page.route('**/api/v1/reliability-auth', r => { count++; return r.fulfill({ status: 401, json: {} }) })
  await page.goto('/summary/b'); const result = await callApi(page, 'get', '/reliability-auth')
  expect(result.ok).toBe(false); expect(count).toBe(1)
  await expect(page.getByRole('button', { name: /登.*录/ }).first()).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('knowall_token'))).toBeNull()
})

test('leaving a summary aborts its pending HTTP read', async ({ page }) => {
  let started = false; let aborted = false
  page.on('requestfailed', r => { if (r.url().endsWith('/knowledge/summary/a')) aborted = true })
  await page.route('**/api/v1/knowledge/summary/a', async r => {
    started = true; await pause(5000); await r.fulfill({ json: summaryData('a', '# 旧资料A') }).catch(() => {})
  })
  await page.goto('/summary/a'); await expect.poll(() => started).toBe(true)
  await switchTo(page, 'b'); await expect(page.locator('.markdown-body')).toContainText('新资料B')
  await expect.poll(() => aborted, { timeout: 1800 }).toBe(true)
})

test('leaving a summary aborts client generation wait without claiming server cancellation', async ({ page }) => {
  let started = false; let aborted = false
  page.on('requestfailed', r => { if (r.url().endsWith('/agents/orchestrate')) aborted = true })
  await page.route('**/api/v1/agents/orchestrate', async r => {
    started = true; await pause(5000); await r.fulfill({ json: plan('旧后台结果') }).catch(() => {})
  })
  await page.goto('/summary/a'); await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await expect.poll(() => started).toBe(true); await switchTo(page, 'b')
  await expect(page.locator('.markdown-body')).toContainText('新资料B')
  await expect.poll(() => aborted, { timeout: 1800 }).toBe(true)
  await expect(page.getByText(/服务端.*已取消|生成失败|生成完成/)).toBeHidden()
})

test('legacy quiz-start GET creates one session even on uncertain 503', async ({ page }) => {
  let count = 0; let params = ''
  await page.route('**/api/v1/quiz/interactive/start?**', r => { count++; params = new URL(r.request().url()).search; return r.fulfill({ status: 503, json: {} }) })
  await page.goto('/summary/b')
  await page.evaluate(async () => {
    const { startInteractiveQuiz } = await import(/* @vite-ignore */ '/src/api.ts')
    await startInteractiveQuiz({ summary_id: 'b', count: 5 }).catch(() => {})
  })
  expect(count).toBe(1); expect(params).toContain('summary_id=b'); expect(params).toContain('count=5')
})

test('native retry honors Retry-After on 429', async ({ page }) => {
  let count = 0
  await page.route('**/api/v1/reliability-throttle', r => {
    count++; return r.fulfill({ status: count === 1 ? 429 : 200, headers: { 'Retry-After': '2' }, json: {} })
  })
  await page.goto('/summary/b'); const result = await callApi(page, 'get', '/reliability-throttle')
  expect(result.ok).toBe(true); expect(count).toBe(2); expect(result.elapsed).toBeGreaterThanOrEqual(1900)
})

test('read retry respects total timeout instead of extending the request', async ({ page }) => {
  let count = 0
  await page.route('**/api/v1/reliability-budget', r => { count++; return r.fulfill({ status: 503, headers: { 'Retry-After': '2' }, json: {} }) })
  await page.goto('/summary/b'); const result = await callApi(page, 'get', '/reliability-budget', undefined, 300)
  expect(result.ok).toBe(false); expect(count).toBe(1); expect(result.elapsed).toBeLessThan(900)
})

test('network failure recovers on a read but never repeats a write', async ({ page }) => {
  await page.goto('/summary/b')
  for (const method of ['get', 'post']) {
    let count = 0
    await page.route('**/api/v1/reliability-network', r => { count++; return count === 1 ? r.abort('failed') : r.fulfill({ json: {} }) })
    const result = await callApi(page, method, '/reliability-network')
    expect(count).toBe(method === 'get' ? 2 : 1); expect(result.ok).toBe(method === 'get')
    await page.unroute('**/api/v1/reliability-network')
  }
})

test('pre-generation coverage cannot replace newer coverage', async ({ page }) => {
  let count = 0; let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const coverage = (rate: number) => ({ full_coverage_rate: rate, total_knowledge_points: 8, covered_by_questions: 4, covered_by_flashcards: 2, uncovered_points: [] })
  await page.route('**/api/v1/knowledge/coverage/a', async r => {
    const n = ++count
    if (n === 1) await gate
    await r.fulfill({ json: coverage(n === 1 ? 0.1 : 0.9) }).catch(() => {})
  })
  await page.route('**/api/v1/agents/orchestrate', r => r.fulfill({ json: plan('更新后计划') }))
  await page.goto('/summary/a'); await expect.poll(() => count).toBe(1)
  await page.getByRole('button', { name: /一键生成全部配套$/ }).first().click()
  await expect(page.getByText(/配套内容生成完成/)).toBeVisible()
  await page.getByRole('tab', { name: /覆盖率$/ }).click(); await expect(page.getByText('90%', { exact: true })).toBeVisible()
  release(); await pause(250); await expect(page.getByText('90%', { exact: true })).toBeVisible()
})

test('rapid double click sends one generation with original source payload', async ({ page }) => {
  let count = 0; let body: any; let authorization = ''
  await page.route('**/api/v1/agents/orchestrate', async r => {
    count++; body = r.request().postDataJSON(); authorization = r.request().headers().authorization
    await pause(300); await r.fulfill({ json: plan('一份计划') })
  })
  await page.goto('/summary/b'); const button = page.getByRole('button', { name: /一键生成全部配套$/ }).first()
  await button.evaluate((el: HTMLElement) => { el.click(); el.click() })
  await expect(page.getByText(/配套内容生成完成/)).toBeVisible(); expect(count).toBe(1)
  expect(body).toEqual({ summary_id: 'b', document_id: 'source-doc', config: { question_count: 30 } }); expect(authorization).toBe(`Bearer ${E2E_TOKEN}`)
})
