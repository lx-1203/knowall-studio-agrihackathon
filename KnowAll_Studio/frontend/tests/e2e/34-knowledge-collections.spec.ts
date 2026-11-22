import { test, expect } from './fixtures'
import { readFile } from 'node:fs/promises'
test.setTimeout(60000)

const points = [
  { id: 'limit', name: '函数极限', topic: '极限', aliases: ['极限'], definition: '函数值在趋近过程中的确定数值。', formulas: ['$\\lim_{x\\to a}f(x)=L$'], conditions: ['左右极限相等'], key_points: ['关注趋近过程'], pitfalls: ['极限不一定等于函数值'], prerequisites: [], related: ['导数'], supplements: [] },
  { id: 'derivative', name: '导数', topic: '微分', aliases: ['微商'], definition: '函数在某点的瞬时变化率。', formulas: ['$f\'(a)=\\lim_{h\\to0}\\frac{f(a+h)-f(a)}h$'], conditions: ['差商极限存在'], key_points: ['可导必连续'], pitfalls: ['连续不一定可导'], prerequisites: ['函数极限'], related: [], supplements: [] },
]
const collection = { id: 'builtin-1', title: '高数基础知识点', subject: '高等数学', kind: 'builtin', review_status: 'compiled', point_count: 2, points }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/knowledge-collections**', route => {
    const path = new URL(route.request().url()).pathname
    return route.fulfill({ json: path.endsWith('/builtin-1') ? collection : path.endsWith('/mine') ? { ...collection, id: 'mine', kind: 'personal', review_status: 'needs_review' } : [collection] })
  })
})

for (const detail of [false, true]) {
  test(`Chinese composition commits only completed text in ${detail ? 'knowledge' : 'collection'} search`, async ({ page }) => {
    await page.goto(detail ? '/knowledge-collections?collection=builtin-1&point=derivative' : '/knowledge-collections', { waitUntil: 'domcontentloaded' })
    const input = page.getByRole('textbox', { name: detail ? '搜索知识点' : '搜索集锦', exact: true })
    await expect(input).toBeVisible()
    await input.focus()
    await input.dispatchEvent('compositionstart', { data: '' })
    await input.evaluate(element => {
      const input = element as HTMLInputElement
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'han')
      input.dispatchEvent(new InputEvent('input', { bubbles: true, data: 'han', inputType: 'insertCompositionText', isComposing: true }))
    })
    await expect(input).toHaveValue('han')
    expect(new URL(page.url()).searchParams.get('q')).toBeNull()
    if (detail) expect(new URL(page.url()).searchParams.get('point')).toBe('derivative')
    // Some browsers end composition without another change event.
    await input.evaluate(element => {
      const input = element as HTMLInputElement
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '函数')
      input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '函数' }))
    })
    await expect(input).toHaveValue('函数')
    await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('函数')
    if (detail) expect(new URL(page.url()).searchParams.get('point')).toBeNull()
    await page.reload()
    await expect(input).toHaveValue('函数')
    await input.fill('TCP')
    await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('TCP')
    await page.locator('.knowledge-compilation-filters .ant-input-clear-icon').click()
    await expect(input).toHaveValue('')
    await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBeNull()
    if (detail) expect(new URL(page.url()).searchParams.get('collection')).toBe('builtin-1')
    await input.fill(detail ? '导数' : '高数')
    if (detail) {
      await page.getByRole('button', { name: '前置知识 函数极限', exact: true }).click()
    } else {
      await page.getByRole('button', { name: '打开 高数基础知识点' }).click()
      await page.goBack()
      await expect(input).toHaveValue('高数')
    }
    if (detail) await expect(input).toHaveValue('')
  })
}

test('knowledge-only search, formulas, prerequisite navigation and export', async ({ page }) => {
  const writes: string[] = []
  page.on('request', req => { if (req.method() === 'POST') writes.push(req.url()) })
  await page.goto('/knowledge-collections', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '知识点集锦', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '打开 高数基础知识点' }).click()
  await page.getByRole('textbox', { name: '搜索知识点' }).fill('微商')
  await page.getByRole('button', { name: '学习 导数', exact: true }).click()
  await expect(page.getByRole('heading', { name: '导数', exact: true })).toBeVisible()
  await expect(page.locator('.knowledge-compilation-detail .katex').first()).toBeVisible()
  await page.getByRole('button', { name: '前置知识 函数极限', exact: true }).click()
  await expect(page.getByRole('heading', { name: '函数极限', exact: true })).toBeVisible()
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出知识点', exact: true }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('高数基础知识点.md')
  const exported = await readFile((await download.path())!, 'utf8')
  expect(exported).toContain('函数在某点的瞬时变化率。')
  expect(exported).not.toMatch(/document_ids|raw_text|原文件/)
  expect(writes.filter(url => url.includes('/knowledge-collections/'))).toHaveLength(0)
  await expect(page.getByRole('button', { name: /原文件|原文|下载资料/ })).toHaveCount(0)
})

test('documents generate only after preview and an explicit click', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [
    { id: 'math', filename: '数学笔记.md', file_type: 'md', status: 'ready' },
  ] }))
  await page.route('**/api/v1/knowledge-collections/preview', route => route.fulfill({ json: {
    batch_count: 2, characters: 14000, chunk_count: 5, documents: [{ id: 'math', name: '数学笔记.md' }],
  } }))
  await page.route('**/api/v1/knowledge-collections/generate', route => {
    calls++
    expect(route.request().postDataJSON().document_ids).toEqual(['math'])
    return route.fulfill({ json: { ...collection, id: 'mine', kind: 'personal', review_status: 'needs_review' } })
  })
  await page.goto('/knowledge-collections')
  await page.getByRole('button', { name: '从资料提炼', exact: true }).click()
  await page.getByRole('textbox', { name: '集锦名称' }).fill('微积分集锦')
  await page.getByRole('textbox', { name: '学科' }).fill('高等数学')
  await page.getByRole('combobox', { name: '选择资料', exact: true }).click()
  await page.getByText('数学笔记.md', { exact: true }).click()
  await page.getByRole('button', { name: '预览处理量', exact: true }).click()
  await expect(page.getByText('预计 2 批提炼', { exact: true })).toBeVisible()
  expect(calls).toBe(0)
  await page.getByRole('button', { name: '开始提炼', exact: true }).click()
  await expect(page.getByText('待校对', { exact: true }).first()).toBeVisible()
  expect(calls).toBe(1)
})

test('load errors can recover and narrow screens retain readable knowledge', async ({ page }) => {
  let failed = true
  await page.route('**/api/v1/knowledge-collections', route => failed
    ? route.fulfill({ status: 503, json: { detail: '暂时不可用' } })
    : route.fulfill({ json: [collection] }))
  await page.goto('/knowledge-collections')
  await expect(page.getByRole('alert').getByText('知识点集锦加载失败')).toBeVisible()
  failed = false
  await page.getByRole('button', { name: '重试加载', exact: true }).click()
  await page.setViewportSize({ width: 375, height: 812 })
  await page.getByRole('button', { name: '打开 高数基础知识点' }).click()
  await page.getByRole('button', { name: '学习 导数', exact: true }).click()
  await expect(page.getByRole('heading', { name: '导数', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

test('personal explanations can be corrected and review status resets on later edits', async ({ page }) => {
  let saved = { ...collection, id: 'mine', kind: 'personal', review_status: 'needs_review' }
  const updates: boolean[] = []
  await page.route('**/api/v1/knowledge-collections/mine', route => {
    if (route.request().method() === 'PUT') {
      const payload = route.request().postDataJSON()
      updates.push(payload.reviewed)
      saved = { ...saved, ...payload, review_status: payload.reviewed ? 'reviewed' : 'needs_review' }
    }
    return route.fulfill({ json: saved })
  })
  await page.goto('/knowledge-collections?collection=mine')
  await page.getByRole('button', { name: '校对知识点', exact: true }).click()
  await page.getByRole('textbox', { name: '校对核心解释' }).fill('校对后的独立解释。')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByRole('article').getByText('校对后的独立解释。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '标记已校对', exact: true }).click()
  await page.getByRole('button', { name: '确认已校对', exact: true }).click()
  await expect(page.locator('.knowledge-compilation-title').getByText('已校对', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('article').getByText('校对后的独立解释。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '校对知识点', exact: true }).click()
  await page.getByRole('textbox', { name: '校对核心解释' }).fill('再次校对后的独立解释。')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.locator('.knowledge-compilation-title').getByText('待校对', { exact: true })).toBeVisible()
  expect(updates).toEqual([false, true, false])
})

test('all shipped knowledge renders formulas, conditions and pitfalls', async ({ page }) => {
  const raw = JSON.parse(await readFile('../backend/app/assets/knowledge_collections.json', 'utf8'))
  // Keep the full catalog covered as authored content grows.
  test.setTimeout(Math.max(60000, raw.reduce((sum: number, entry: typeof collection) => sum + entry.points.length, 0) * 2000))
  const shipped = raw.map((entry: typeof collection, index: number) => ({ ...entry,
    id: `builtin-${index + 1}`, kind: 'builtin', review_status: 'compiled', point_count: entry.points.length,
    points: entry.points.map((point, p) => ({ aliases: [], formulas: [], prerequisites: [], related: [], supplements: [], ...point, id: `point-${p}` })),
  }))
  await page.route('**/api/v1/knowledge-collections**', route => {
    const id = new URL(route.request().url()).pathname.split('/').pop()
    return route.fulfill({ json: shipped.find((entry: typeof collection) => entry.id === id) || shipped })
  })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.goto('/knowledge-collections')
  await expect(page.getByRole('button', { name: /^打开 / })).toHaveCount(shipped.length)
  await page.screenshot({ path: '.tmp/knowledge-catalog.png', fullPage: true, animations: 'disabled' })
  let count = 0
  for (const entry of shipped) {
    await page.goto(`/knowledge-collections?collection=${entry.id}`)
    for (const point of entry.points) {
      await page.getByRole('button', { name: `学习 ${point.name}`, exact: true }).click()
      await expect(page.getByRole('article').getByRole('heading', { name: point.name, exact: true })).toBeVisible()
      await expect(page.getByRole('article').getByRole('heading', { name: '适用条件', exact: true })).toBeVisible()
      await expect(page.getByRole('article').getByRole('heading', { name: '易错点', exact: true })).toBeVisible()
      if (point.formulas.length) await expect(page.locator('.knowledge-compilation-detail .katex').first()).toBeVisible()
      await expect(page.locator('.knowledge-compilation-detail .katex-error')).toHaveCount(0)
      count++
    }
  }
  expect(count).toBe(raw.reduce((sum: number, entry: typeof collection) => sum + entry.points.length, 0))
  expect(count).toBeGreaterThanOrEqual(32)
  await page.goto('/knowledge-collections?collection=builtin-1&point=point-2')
  await expect(page.getByRole('article').getByRole('heading', { name: '导数', exact: true })).toBeVisible()
  await expect(page.locator('.knowledge-compilation-detail .katex').first()).toBeVisible()
  await page.screenshot({ path: '.tmp/knowledge-detail.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await page.screenshot({ path: '.tmp/knowledge-dark.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '切换到浅色模式' }).click()
  await page.setViewportSize({ width: 375, height: 812 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: '.tmp/knowledge-mobile.png', fullPage: true, animations: 'disabled' })
})
