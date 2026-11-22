/**
 * Runtime coverage for the lazily-imported modules introduced by B16/B17/B18/B68.
 *
 * Why this file exists
 * -------------------
 * B16 (`react-markdown`), B17 (`antd/es/table`), B18 (`antd/es/date-picker`)
 * and B68 (`KnowledgeGraph`)
 * moved heavy dependencies behind `React.lazy()`. The pre-existing specs
 * (03-knowledge, 04-quiz, 09-study) only assert that *some* card renders — none
 * of them ever switches a tab or opens a modal, so **not one of them would fail
 * if a lazy chunk 404'd and the Suspense fallback stayed on screen forever.**
 *
 * That is exactly the bug class lazy-loading introduces: the page still looks
 * "fine" (a spinner or a disabled input is visible), the build is green, and the
 * bundle budget passes. The only way to catch it is to *activate* the path and
 * assert the real component replaced its fallback.
 *
 * Every test asserts **two independent properties**:
 *
 *   1. **Deferral** — the chunk is NOT requested before the path is activated.
 *      If someone hoists the dynamic import back into the entry, this goes red.
 *      This is the actual claim B16/B17/B18/B68 make, so it is worth pinning down.
 *   2. **Resolution** — after activation the chunk IS requested, and the DOM
 *      shows the real component rather than its fallback. Each fallback was
 *      chosen to be deliberately *distinguishable* from the real thing
 *      (disabled input / bare <Spin> / no markdown headings).
 *
 * Each assertion below was verified to be capable of failing — see the mutation
 * run recorded in docs/optimization-report.md §21. A test that cannot go red is
 * not evidence.
 */
import { test, expect, type Page } from './fixtures'

/** Record every request URL matching any of `patterns` (cumulative). */
function trackRequests(page: Page, patterns: RegExp[]) {
  const seen: string[] = []
  page.on('request', (req) => {
    if (patterns.some((p) => p.test(req.url()))) seen.push(req.url())
  })
  return seen
}

// Vite dev pre-bundles each dynamic import into its own module URL, e.g.
// /node_modules/.vite/deps/antd_es_date-picker.js — verified by observing real
// requests, not assumed from the import specifier.
const DATE_PICKER = [/deps\/antd_es_date-picker\.js/, /deps\/rc-picker\.js/]
const ANTD_TABLE = [/deps\/antd_es_table\.js/, /deps\/rc-table\.js/]
const REACT_MARKDOWN = [/deps\/react-markdown\.js/, /deps\/micromark\.js/]
const KNOWLEDGE_GRAPH = [/\/src\/pages\/KnowledgeGraph\.tsx(?:\?|$)/]

test.describe('lazy chunks: deferred on first paint, resolved on activation', () => {
  test('B18 · StudyPage DatePicker loads only when the modal opens', async ({ page }) => {
    const hits = trackRequests(page, DATE_PICKER)

    await page.goto('/study')
    await page.waitForLoadState('networkidle')

    // (1) Deferral — nothing on the first paint pulls the picker in.
    expect(hits, 'B18 claim broken: date-picker chunk is on the first-paint path').toHaveLength(0)

    await page.getByRole('button', { name: 'New Plan' }).click()
    await expect(page.getByText('New Study Plan')).toBeVisible()

    // The Suspense fallback is `<Input placeholder="Target end date" disabled />`.
    // The real DatePicker reuses that placeholder but is NOT disabled, so
    // "visible AND enabled" can only be true once the lazy chunk has resolved.
    const field = page.locator('input[placeholder="Target end date"]')
    await expect(field).toBeVisible()
    await expect(field, 'still the disabled Suspense fallback — chunk never resolved').toBeEnabled()

    // (2) Resolution — the chunk really was fetched when the modal opened.
    expect(hits.length, 'date-picker chunk was never requested').toBeGreaterThan(0)
  })

  test('B17 · QuizPage Table is deferred until the 题库 tab is opened', async ({ page }) => {
    const hits = trackRequests(page, ANTD_TABLE)

    await page.goto('/quiz')
    await page.waitForLoadState('networkidle')

    // (1) Deferral — the Table lives only in the 题库 tab, so arriving on the
    // page must not drag rc-table in.
    expect(hits, 'B17 claim broken: rc-table is on the first-paint path').toHaveLength(0)

    await page.getByRole('tab', { name: /题库/ }).click()

    // (2) Resolution — `antd` renders `.ant-table` only from the real component;
    // the Suspense fallback is a bare <Spin> and can never produce it.
    await expect(page.locator('.ant-table').first()).toBeVisible({ timeout: 15000 })
    expect(hits.length, 'antd/es/table chunk was never requested').toBeGreaterThan(0)
  })

  test('B16 · KnowledgePage ReactMarkdown renders real markdown in the 原生大纲 tab', async ({ page }) => {
    const hits = trackRequests(page, REACT_MARKDOWN)

    // Seed the two endpoints this path depends on. Registered after the shared
    // fixture's blanket `**/api/**` stub, and Playwright prefers the most
    // recently registered matching route — so these win.
    await page.route('**/api/v1/documents/?*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [{ id: 'doc-1', filename: 'outline.md', file_type: 'md', status: 'done' }],
        }),
      }),
    )
    await page.route('**/api/v1/documents/doc-1/native-outline', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          outline_markdown: '# E2E 原生大纲标题\n\n这是一段由 react-markdown 渲染的正文。',
          headings: [],
          image_count: 0,
        }),
      }),
    )

    // `selectedDoc` lives in a plain (non-persisted) zustand store, so it can
    // only be set through the UI. The 出题 tab's document picker is the cheapest
    // route to it.
    await page.goto('/quiz')
    await page.waitForLoadState('networkidle')
    await page.locator('.ant-select', { hasText: '选择出题素材文档' }).first().click()
    await page.getByTitle('outline.md').click()

    // Client-side navigation only — a full reload would reset the in-memory store.
    await page.getByRole('menuitem', { name: '更多工具', exact: true }).click()
    await page.getByRole('link', { name: '思维导图' }).click()

    // Wait for KnowledgePage itself to mount before asserting deferral.
    // Without this wait the assertion runs while the page module is still
    // being fetched, so it is vacuously true — and this test then cannot fail
    // even if react-markdown is hoisted into the page's static graph.
    // (Caught by mutation testing: see docs/optimization-report.md §21.)
    await expect(page.getByRole('tab', { name: '原生大纲' })).toBeVisible({ timeout: 15000 })

    // (1) Deferral — sitting on KnowledgePage does not pull markdown in; only
    // the 原生大纲 tab does.
    expect(hits, 'B16 claim broken: react-markdown is on the first-paint path').toHaveLength(0)

    await page.getByRole('tab', { name: '原生大纲' }).click()

    // (2) Resolution — only react-markdown turns `# …` into a real <h1>; the
    // Suspense fallback is a <Spin> and would leave the raw '# ' visible as text.
    await expect(
      page.getByRole('heading', { name: 'E2E 原生大纲标题' }),
      'react-markdown did not render — the lazy chunk likely failed to resolve',
    ).toBeVisible({ timeout: 15000 })
    expect(hits.length, 'react-markdown chunk was never requested').toBeGreaterThan(0)
  })

  test('B68 · Knowledge graph loads only after a tree is selected', async ({ page }) => {
    const hits = trackRequests(page, KNOWLEDGE_GRAPH)
    await page.route('**/api/v1/knowledge/trees*', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ tree_id: 'e2e-tree', name: 'E2E 知识树' }]),
    }))
    await page.route('**/api/v1/knowledge/tree/e2e-tree', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tree_data: {
          nodes: [
            { id: 'root', label: 'E2E 根节点', children: [
              { id: 'child', label: 'E2E 子节点', children: [] },
            ] },
          ],
        },
      }),
    }))
    await page.route('**/api/v1/knowledge/edges/e2e-tree', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    }))

    await page.goto('/knowledge')
    await expect(page.getByRole('tab', { name: 'AI 知识树' })).toBeVisible()
    await page.waitForLoadState('networkidle')
    expect(hits, 'graph chunk loaded before any tree was selected').toHaveLength(0)

    await page.locator('.ant-select', { hasText: '历史知识树' }).click()
    await page.locator('.ant-select-item-option[title="E2E 知识树"]').click()
    await expect(page.locator('.react-flow__node')).toHaveCount(2)
    await expect(page.getByText('E2E 根节点')).toBeVisible()
    expect(hits.length, 'graph chunk was not fetched after selecting the tree').toBeGreaterThan(0)
  })
})
