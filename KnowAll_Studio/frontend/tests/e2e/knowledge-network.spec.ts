import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

// Browser integration evidence uses explicit API mocks; no real AI/backend calls.
const item = (id: string, name: string, review_status = 'reviewed') => ({ id, name, subject: '高等数学', topic: '微积分', definition: `${name}的中央解释。`, aliases: [], review_status, in_learning: false, source_count: 0 })
const content = (id: string, name: string) => ({ id, name, topic: '微积分', definition: `${name}的中央解释。`, aliases: [], formulas: ['$f\'(x)=\\lim_{h\\to0}\\frac{f(x+h)-f(x)}{h}$'], conditions: ['极限存在'], key_points: ['研究瞬时变化'], pitfalls: ['连续未必可导'], prerequisites: [], related: [], supplements: [] })
async function switchAccount(page: Page) {
  await page.evaluate(async built => {
    // Exercise the same live store in dev and built artifacts without a test hook.
    const url = built ? document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src : '/src/stores/index.ts'
    if (!url) throw new Error('Application module not found')
    const exports = await import(url)
    const store = Object.values(exports).find((value: any) => {
      const state = typeof value?.getState === 'function' ? value.getState() : null
      return typeof state?.login === 'function' && typeof state?.logout === 'function'
    }) as any
    if (!store) throw new Error('Live authentication store not found')
    store.getState().login('next-account-token', { id: 'next-account', username: 'next', email: 'next@example.com', created_at: '' })
  }, process.env.KNOWLEDGE_NETWORK_BUILT === '1')
}
async function network(page: Page) {
  const calls: { scope: string | null; q: string | null }[] = []
  const learning: { saved: boolean; mastery: string; note: string }[] = []; const publishes: { reviewed: boolean }[] = []
  let state = { saved: false, mastery: 'unseen', note: '' }
  let published = false
  await page.route('**/api/v1/knowledge-network/**', route => {
    const url = new URL(route.request().url()), path = url.pathname
    if (path.endsWith('/documents')) return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/discover')) {
      calls.push({ scope: url.searchParams.get('scope'), q: url.searchParams.get('q') })
      const rows = url.searchParams.get('scope') === 'personal' ? [item('candidate', '积分', published ? 'reviewed' : 'needs_review')] : [item('derivative', '导数')]
      return route.fulfill({ json: { items: rows, total: rows.length } })
    }
    const id = path.split('/concepts/')[1]?.split('/')[0] || 'derivative'
    const name = id === 'candidate' ? '积分' : id === 'limit' ? '函数极限' : '导数'
    if (path.endsWith('/learning')) { state = route.request().postDataJSON(); learning.push(state); return route.fulfill({ json: state }) }
    if (path.endsWith('/publish')) { publishes.push(route.request().postDataJSON()); published = true }
    return route.fulfill({ json: { ...item(id, name, id === 'candidate' && !published ? 'needs_review' : 'reviewed'), content: content(id, name),
      can_contribute: id === 'candidate', sources: id === 'candidate' ? [{ document_id: 'my-doc', filename: '我的积分笔记.md', chunk_id: 'real-chunk', page_range: '3-4', quote: '积分是累积量。', definition: '我的资料中的积分解释。' }] : [],
      relations: id === 'derivative' ? [{ source_id: 'derivative', target_id: 'limit', relation_type: 'prerequisite', target_name: '函数极限' }] : [], learning: state } })
  })
  return { calls, learning, publishes }
}

test('central search, real concept navigation, private learning persistence', async ({ page }) => {
  const api = await network(page)
  await page.goto('/knowledge-network?scope=public&q=导数')
  await expect(page.getByRole('heading', { name: '知识库', exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '搜索知识库' }).fill('导数')
  await page.getByRole('button', { name: '检索', exact: true }).click()
  await expect.poll(() => api.calls.some(c => c.scope === 'public' && c.q === '导数')).toBeTruthy()
  await page.getByRole('button', { name: '学习 导数', exact: true }).click()
  await expect(page.getByRole('article').getByRole('heading', { name: '导数', exact: true })).toBeVisible()
  await expect(page.getByRole('article').locator('.katex').first()).toBeVisible()
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await expect(page.getByText('来源搜索中', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await page.getByRole('button', { name: '收藏到我的学习库', exact: true }).click()
  await expect.poll(() => api.learning.at(-1)?.saved).toBe(true)
  await page.getByRole('combobox', { name: '掌握状态' }).focus()
  await page.getByRole('combobox', { name: '掌握状态' }).press('ArrowDown')
  await page.locator('.ant-select-item-option-content').getByText('已掌握', { exact: true }).click()
  await page.getByRole('textbox', { name: '私有学习笔记' }).fill('我的学习心得')
  await page.getByRole('button', { name: '保存学习记录', exact: true }).click()
  await expect.poll(() => api.learning.at(-1)?.note).toBe('我的学习心得')
  expect(api.learning.at(-1)?.mastery).toBe('mastered')
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await page.getByRole('button', { name: '前置知识 函数极限', exact: true }).click()
  await expect(page).toHaveURL(/concept=limit/)
  await expect(page.getByRole('article').getByRole('heading', { name: '函数极限', exact: true })).toBeVisible()
})

test('personal candidate never presents uploaded material as authority and explicitly contributes after review', async ({ page }) => {
  const api = await network(page)
  await page.goto('/knowledge-network?scope=public&q=导数')
  await page.getByRole('button', { name: '我的知识', exact: true }).click()
  await expect.poll(() => api.calls.at(-1)?.scope).toBe('personal')
  await page.getByRole('button', { name: '学习 积分', exact: true }).click()
  await expect(page.getByRole('article').getByText('待校对', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await expect(page.getByText('来源搜索中', { exact: true })).toBeVisible()
  await expect(page.getByRole('article')).not.toContainText('积分是累积量。')
  await expect(page.getByRole('article')).not.toContainText('我的积分笔记.md')
  await expect(page.getByRole('article')).not.toContainText('我的资料中的积分解释。')
  await expect(page.getByRole('article')).not.toContainText('real-chunk')
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await page.getByRole('button', { name: '校对并贡献', exact: true }).click()
  await expect(page.getByText('已有中央定义会保留，学习笔记只对你可见。')).toBeVisible()
  expect(api.publishes).toHaveLength(0)
  await page.getByRole('checkbox', { name: '我已核对概念摘要与关系，确认贡献到中央知识库' }).check()
  await page.getByRole('button', { name: '确认贡献', exact: true }).click()
  await expect.poll(() => api.publishes.length).toBe(1)
  expect(api.publishes[0]).toEqual({ reviewed: true })
})

for (const failed of [false, true]) test(`upload shows automatic extraction ${failed ? 'failure and retry' : 'progress and result'}`, async ({ page }) => {
  let polls = 0, retries = 0
  const job = { id: 'job', document_id: 'uploaded', status: failed ? 'failed' : 'running', point_count: 0, error: failed ? '暂时无法提炼' : null }
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'uploaded', filename: 'notes.md', total_chunks: 1, knowledge_extraction: job } }))
  await page.route('**/api/v1/knowledge-network/extractions**', r => {
    if (r.request().method() === 'POST') { retries++; expect(r.request().postDataJSON()).toEqual({ document_id: 'uploaded' }); return r.fulfill({ status: 202, json: { ...job, status: 'running', error: null } }) }
    polls++
    return r.fulfill({ json: { ...job, status: polls > 1 ? 'completed' : 'running', point_count: polls > 1 ? 2 : 0, error: null, summary_id: 'summary-result' } })
  })
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('函数极限与导数') })
  if (failed) {
    await expect(page.getByText('暂时无法提炼', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '重试知识提炼', exact: true }).click()
    expect(retries).toBe(1)
  }
  await expect(page.getByText('正在提炼关键知识点', { exact: true })).toBeVisible()
  await expect(page.getByText('已提炼 2 个知识点，待你校对。', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '查看这份资料的知识', exact: true })).toHaveAttribute('href', '/knowledge-network?scope=personal&document=uploaded')
  await expect(page.getByRole('link', { name: '查看知识总纲', exact: true })).toHaveAttribute('href', '/summary/summary-result')
})

test('narrow screen keeps readable details and list failure recovers', async ({ page }) => {
  await network(page)
  let failure = true
  await page.route('**/api/v1/knowledge-network/discover?**', r => failure ? r.fulfill({ status: 503, json: { detail: '知识网络暂不可用' } }) : r.fulfill({ json: { items: [item('derivative', '导数')], total: 1 } }))
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/knowledge-network?scope=public&q=导数')
  await expect(page.getByText('知识网络暂不可用', { exact: true })).toBeVisible()
  failure = false
  await page.getByRole('button', { name: '重试加载', exact: true }).click()
  await page.getByRole('button', { name: '学习 导数', exact: true }).click()
  await expect(page.getByRole('article').getByRole('heading', { name: '导数', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('tab', { name: '知识讲解', exact: true }).click()
  await expect(page.getByRole('article').locator('.katex').first()).toBeVisible()
  await page.screenshot({ path: '.tmp/knowledge-network-mobile.png', fullPage: true })
})

test('old account request cannot populate the next account', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await network(page)
  await page.route('**/api/v1/knowledge-network/discover?**', async r => {
    if (r.request().headers().authorization?.includes('next-account')) return r.fulfill({ json: { items: [item('new', '新账号概念')], total: 1 } })
    await gate
    await r.fulfill({ json: { items: [item('old', '旧账号私有概念')], total: 1 } }).catch(() => {})
  })
  await page.goto('/knowledge-network?scope=personal&q=积分', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '知识库', exact: true })).toBeVisible()
  await switchAccount(page)
  release()
  await expect(page.getByRole('button', { name: '学习 新账号概念', exact: true })).toBeVisible()
  await expect(page.getByText('旧账号私有概念', { exact: true })).toHaveCount(0)
})

test('source navigation selects only a current document chunk and unknown source falls back', async ({ page }) => {
  await page.route('**/api/v1/documents/my-doc', r => r.fulfill({ json: { id: 'my-doc', filename: '我的积分笔记.md', file_type: 'md', status: 'ready', subject: '数学' } }))
  await page.route('**/api/v1/documents/my-doc/chunks', r => r.fulfill({ json: [
    { id: 'first-chunk', text: '第一段基础内容。', page_range: '1' },
    { id: 'real-chunk', text: '积分是累积量。', page_range: '3-4' },
  ] }))
  await page.goto('/materials/my-doc?source=real-chunk')
  await expect(page.locator('.material-reader')).toContainText('积分是累积量。')
  await expect(page.locator('.material-reader-pagination')).toContainText('2 / 2')
  await page.goto('/materials/my-doc?source=foreign-chunk')
  await expect(page.getByText('对应原文片段已变化，请从目录查看当前资料。', { exact: true })).toBeVisible()
  await expect(page.locator('.material-reader')).toContainText('积分是累积量。')
})


test('compiled concepts show their actual review status', async ({ page }) => {
  await network(page)
  await page.route('**/api/v1/knowledge-network/concepts/builtin*', r => r.fulfill({ json: { ...item('builtin', '函数极限', 'compiled'), content: content('builtin', '函数极限'), sources: [], relations: [], learning: null } }))
  await page.goto('/knowledge-network?concept=builtin&q=导数')
  await expect(page.getByRole('article').getByText('内置整理', { exact: true })).toBeVisible()
  await expect(page.getByRole('article').getByText('已校对贡献', { exact: true })).toHaveCount(0)
})

test('late private learning write cannot populate another concept', async ({ page }) => {
  await network(page)
  let release!: () => void, started = false
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/knowledge-network/concepts/derivative/learning', async r => {
    started = true; await gate
    await r.fulfill({ json: { saved: true, mastery: 'mastered', note: '旧概念笔记' } }).catch(() => {})
  })
  await page.goto('/knowledge-network?concept=derivative&q=导数')
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await page.getByRole('textbox', { name: '私有学习笔记' }).fill('旧概念笔记')
  await page.getByRole('button', { name: '保存学习记录', exact: true }).click()
  await expect.poll(() => started).toBe(true)
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await page.getByRole('button', { name: '前置知识 函数极限', exact: true }).click()
  release()
  await expect(page.getByRole('article').getByRole('heading', { name: '函数极限', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '私有学习笔记' })).toHaveValue('')
  await expect(page.getByRole('button', { name: '收藏到我的学习库', exact: true })).toBeEnabled()
})

test('account switch discards a late extraction result', async ({ page }) => {
  let release!: () => void, polled = false
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'old-doc', filename: 'notes.md', total_chunks: 1, knowledge_extraction: { id: 'old-job', document_id: 'old-doc', status: 'running', point_count: 0, error: null } } }))
  await page.route('**/api/v1/knowledge-network/extractions/old-job', async r => {
    polled = true; await gate
    await r.fulfill({ json: { id: 'old-job', document_id: 'old-doc', status: 'completed', point_count: 99, error: null } }).catch(() => {})
  })
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('old') })
  await expect.poll(() => polled).toBe(true)
  await switchAccount(page)
  release()
  await expect(page.getByRole('region', { name: '知识提炼进度' })).toHaveCount(0)
  await expect(page.getByText('已提炼 99 个知识点，待你校对。', { exact: true })).toHaveCount(0)
})

test('legacy upload responses remain compatible without an extraction field', async ({ page }) => {
  const indexed: string[] = []
  await page.route('**/api/v1/search/index?*', r => { indexed.push(new URL(r.request().url()).searchParams.get('doc_id')!); return r.fulfill({ json: { indexed: 1 } }) })
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'legacy-doc', filename: 'legacy.md', total_chunks: 1 } }))
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: 'legacy.md', mimeType: 'text/markdown', buffer: Buffer.from('legacy') })
  await expect(page.getByText('上传成功: legacy.md (1 个分片)', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '知识提炼进度' })).toHaveCount(0)
  await expect(page.getByText('页面出错了', { exact: false })).toHaveCount(0)
  await expect.poll(() => indexed).toEqual(['legacy-doc'])
})

test('owned extraction upload skips global indexing', async ({ page }) => {
  const indexed: string[] = []
  await page.route('**/api/v1/search/index?*', r => { indexed.push(new URL(r.request().url()).searchParams.get('doc_id')!); return r.fulfill({ status: 403, json: { detail: 'Owned documents cannot be globally indexed' } }) })
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: 'owned-doc', filename: 'owned.md', total_chunks: 1, knowledge_extraction: { id: 'owned-job', document_id: 'owned-doc', status: 'completed', point_count: 2, error: null } } }))
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: 'owned.md', mimeType: 'text/markdown', buffer: Buffer.from('private notes') })
  await expect(page.getByText('已提炼 2 个知识点，待你校对。', { exact: true })).toBeVisible()
  await expect(page.locator('.ant-upload-text button')).toBeEnabled()
  expect(indexed).toEqual([])
})

test('authoritative search results render safe public links without uploaded sources', async ({ page }) => {
  await network(page)
  await page.route('**/api/v1/knowledge-network/concepts/authority*', r => r.fulfill({ json: { ...item('authority', '导数'), content: content('authority', '导数'),
    sources: [{ filename: 'PRIVATE-UPLOAD.pdf', quote: 'PRIVATE-QUOTE', definition: 'PRIVATE-DEFINITION', chunk_id: 'private-chunk', document_id: 'private-doc', page_range: '5' }],
    source_search: { status: 'found', sources: [{ title: '公开教材：导数', url: 'https://example.edu/calculus/derivative', authority: '大学公开教材', excerpt: '导数描述瞬时变化率。' }, { title: '不安全链接', url: 'javascript:alert(1)', authority: '未知' }] },
    relations: [], learning: null } }))
  await page.goto('/knowledge-network?concept=authority&q=导数')
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  const link = page.getByRole('link', { name: '公开教材：导数', exact: true })
  await expect(link).toHaveAttribute('href', 'https://example.edu/calculus/derivative')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(page.getByText('大学公开教材', { exact: true })).toBeVisible()
  await expect(page.getByText('导数描述瞬时变化率。', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '不安全链接' })).toHaveCount(0)
  await expect(page.getByRole('article')).not.toContainText('PRIVATE-')
  await page.getByRole('tab', { name: '知识讲解', exact: true }).click()
  await expect(page.getByRole('article').locator('.katex').first()).toBeVisible()
  await page.screenshot({ path: '.tmp/knowledge-network-authority-desktop.png', fullPage: true })
})

test('authority lookup can retry without treating an uploaded document as a source', async ({ page }) => {
  await network(page)
  let lookups = 0
  await page.route('**/api/v1/knowledge-network/concepts/derivative/sources/search', r => {
    lookups++
    return r.fulfill({ json: { status: 'found', sources: [{ title: '权威公开资料', url: 'https://example.edu/calculus', authority: '公开课程' }] } })
  })
  await page.goto('/knowledge-network?concept=derivative&q=导数')
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await expect(page.getByText('来源搜索中', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '高级检索权威来源', exact: true }).click()
  await expect(page.getByRole('link', { name: '权威公开资料', exact: true })).toHaveAttribute('href', 'https://example.edu/calculus')
  expect(lookups).toBe(1)
})

test('source search polls until found while preserving an unsaved private note', async ({ page }) => {
  await network(page)
  let started = false, reads = 0
  await page.route('**/api/v1/knowledge-network/concepts/derivative*', r => {
    reads++
    return r.fulfill({ json: { ...item('derivative', '导数'), content: content('derivative', '导数'), sources: [], relations: [], learning: { saved: false, mastery: 'unseen', note: '服务端旧笔记' },
      source_search: started && reads > 1 ? { status: 'found', sources: [{ title: '检索后的权威来源', url: 'https://example.edu/math', authority: '公开教材' }] } : { status: 'searching', sources: [] } } })
  })
  await page.route('**/api/v1/knowledge-network/concepts/derivative/sources/search', r => { started = true; return r.fulfill({ json: { status: 'searching', sources: [] } }) })
  await page.goto('/knowledge-network?concept=derivative&q=导数')
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await page.getByRole('textbox', { name: '私有学习笔记' }).fill('尚未保存的学习想法')
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await page.getByRole('button', { name: '高级检索权威来源', exact: true }).click()
  await expect(page.getByRole('link', { name: '检索后的权威来源', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '私有学习笔记' })).toHaveValue('尚未保存的学习想法')
})

test('a late source poll cannot reveal the previous account result', async ({ page }) => {
  await network(page)
  let release!: () => void, reads = 0, polling = false
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/knowledge-network/concepts/derivative*', async r => {
    if (r.request().headers().authorization?.includes('next-account')) return r.fulfill({ json: { ...item('derivative', '新账号概念'), content: content('derivative', '新账号概念'), sources: [], relations: [], learning: null, source_search: { status: 'found', sources: [{ title: '新账号权威来源', url: 'https://example.edu/new', authority: '公开教材' }] } } })
    reads++
    if (reads === 1) return r.fulfill({ json: { ...item('derivative', '导数'), content: content('derivative', '导数'), sources: [], relations: [], learning: null, source_search: { status: 'searching', sources: [] } } })
    polling = true; await gate
    await r.fulfill({ json: { ...item('derivative', '旧账号概念'), source_search: { status: 'found', sources: [{ title: '旧账号来源', url: 'https://example.edu/old', authority: '公开教材' }] } } }).catch(() => {})
  })
  await page.goto('/knowledge-network?concept=derivative&q=导数')
  await expect.poll(() => polling).toBe(true)
  await switchAccount(page)
  release()
  await page.getByRole('tab', { name: '关联与来源', exact: true }).click()
  await expect(page.getByRole('link', { name: '新账号权威来源', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '旧账号来源', exact: true })).toHaveCount(0)
})

test('central and personal details explicitly request their own definition scope', async ({ page }) => {
  await network(page)
  const scopes: (string | null)[] = []
  await page.route('**/api/v1/knowledge-network/concepts/derivative*', r => {
    const scope = new URL(r.request().url()).searchParams.get('scope'); scopes.push(scope)
    const definition = scope === 'public' ? '中央公开解释' : '我的个人解释'
    return r.fulfill({ json: { ...item('derivative', '导数'), definition, content: { ...content('derivative', '导数'), definition }, sources: [], relations: [], learning: null } })
  })
  await page.goto('/knowledge-network?scope=public&concept=derivative&q=导数')
  await expect(page.getByRole('article')).toContainText('中央公开解释')
  await expect(page.getByRole('article')).not.toContainText('我的个人解释')
  await page.goto('/knowledge-network?scope=personal&concept=derivative&q=导数')
  await expect(page.getByRole('article')).toContainText('我的个人解释')
  expect(scopes).toEqual(['public', 'personal'])
})

test('public contribution keeps the displayed central definition', async ({ page }) => {
  await network(page)
  let published = false
  const current = { ...item('derivative', '导数'), content: { ...content('derivative', '导数'), definition: '中央定义保持不变' }, can_contribute: true, sources: [], relations: [], learning: null }
  await page.route('**/api/v1/knowledge-network/concepts/derivative*', r => r.fulfill({ json: current }))
  await page.route('**/api/v1/knowledge-network/concepts/derivative/publish', r => {
    published = true
    return r.fulfill({ json: { ...current, content: { ...current.content, definition: '本人资料的不同解释' } } })
  })
  await page.goto('/knowledge-network?scope=public&concept=derivative&q=导数')
  await page.getByRole('tab', { name: '学习记录', exact: true }).click()
  await page.getByRole('button', { name: '校对并贡献', exact: true }).click()
  await page.getByRole('checkbox', { name: '我已核对概念摘要与关系，确认贡献到中央知识库' }).check()
  await page.getByRole('button', { name: '确认贡献', exact: true }).click()
  await expect(page.getByText('已贡献到中央知识库', { exact: true })).toBeVisible()
  expect(published).toBe(true)
  await expect(page.getByRole('article')).toContainText('中央定义保持不变')
  await expect(page.getByRole('article')).not.toContainText('本人资料的不同解释')
})

test('deleting a pending document removes its extraction and stops stale status UI', async ({ page }) => {
  let deleted = false
  const doc = { id: 'deleted-doc', filename: 'delete-me.md', file_type: 'md', status: 'ready', page_count: 1, created_at: '' }
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ json: deleted ? [] : [doc] }))
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: doc.id, filename: doc.filename, total_chunks: 1, knowledge_extraction: { id: 'delete-job', document_id: doc.id, status: 'running', point_count: 0, error: null } } }))
  await page.route('**/api/v1/knowledge-network/extractions/delete-job', r => r.fulfill({ json: { id: 'delete-job', document_id: doc.id, status: 'running', point_count: 0, error: null } }))
  await page.route('**/api/v1/documents/deleted-doc', r => { deleted = true; return r.fulfill({ json: { status: 'deleted' } }) })
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: doc.filename, mimeType: 'text/markdown', buffer: Buffer.from('delete') })
  await expect(page.getByText('正在提炼关键知识点', { exact: true })).toBeVisible()
  await page.locator('.ant-table-row').filter({ hasText: doc.filename }).getByRole('button', { name: 'delete', exact: true }).click()
  await page.getByRole('button', { name: '确 定', exact: true }).click()
  await expect.poll(() => deleted).toBe(true)
  await expect(page.getByRole('region', { name: '知识提炼进度' })).toHaveCount(0)
})

test('deleting a document aborts its in-flight extraction retry and cannot resurrect it', async ({ page }) => {
  let deleted = false, started = false, release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const doc = { id: 'retry-delete-doc', filename: 'retry-delete.md', file_type: 'md', status: 'ready', page_count: 1, created_at: '' }
  const job = { id: 'retry-delete-job', document_id: doc.id, status: 'failed', point_count: 0, error: '请重试' }
  await page.route('**/api/v1/documents/?*', r => r.fulfill({ json: deleted ? [] : [doc] }))
  await page.route('**/api/v1/documents/upload', r => r.fulfill({ json: { document_id: doc.id, filename: doc.filename, total_chunks: 1, knowledge_extraction: job } }))
  await page.route('**/api/v1/knowledge-network/extractions', async r => { started = true; await gate; await r.fulfill({ json: { ...job, status: 'completed', point_count: 22, error: null } }).catch(() => {}) })
  await page.route('**/api/v1/documents/retry-delete-doc', r => { deleted = true; return r.fulfill({ json: { status: 'deleted' } }) })
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: doc.filename, mimeType: 'text/markdown', buffer: Buffer.from('delete retry') })
  await page.getByRole('button', { name: '重试知识提炼', exact: true }).click()
  await expect.poll(() => started).toBe(true)
  await page.locator('.ant-table-row').filter({ hasText: doc.filename }).getByRole('button', { name: 'delete', exact: true }).click()
  await page.getByRole('button', { name: '确 定', exact: true }).click()
  await expect.poll(() => deleted).toBe(true)
  release()
  await expect(page.getByRole('region', { name: '知识提炼进度' })).toHaveCount(0)
  await expect(page.getByText('已提炼 22 个知识点，待你校对。', { exact: true })).toHaveCount(0)
})
