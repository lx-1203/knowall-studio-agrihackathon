import { test, expect } from './fixtures'

const task = (id: string, status = 'researching') => ({
  task_id: id, topic: `研究${id}`, refined_topic: `研究${id}`, status, progress: status === 'completed' ? 1 : 0.4,
  sub_topics: [], report: status === 'completed' ? `${id}研究报告` : '', sources: [],
})
const book = (id: string, status = 'compiling') => ({
  id, title: `教材${id}`, status, progress: status === 'completed' ? 1 : 0.4,
  chapters: [], chapter_count: 0,
})

test('new research displays its accepted task immediately', async ({ page }) => {
  await page.clock.install()
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [] } }))
  await page.route('**/api/v1/research/start', route => route.fulfill({ json: task('新任务', 'pending') }))
  await page.route('**/api/v1/research/*/status', route => route.fulfill({ json: task('新任务') }))
  await page.goto('/research')
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000))
  await page.getByPlaceholder(/例如：大语言模型/).fill('细胞研究')
  await page.getByRole('button', { name: '开始研究' }).click()
  await expect(page.locator('.ant-card-head-title').filter({ hasText: '研究新任务' })).toBeVisible()
})

test('viewing completed research stops previous task polling', async ({ page }) => {
  await page.clock.install()
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [task('B', 'completed')] } }))
  await page.route('**/api/v1/research/start', route => route.fulfill({ json: task('A') }))
  await page.route('**/api/v1/research/*/status', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!
    return route.fulfill({ json: task(id, id === 'B' ? 'completed' : 'researching') })
  })
  await page.goto('/research')
  await page.getByPlaceholder(/例如：大语言模型/).fill('连续任务研究')
  await page.getByRole('button', { name: '开始研究' }).click()
  await page.clock.runFor(2100)
  await expect(page.locator('.ant-card-head-title').filter({ hasText: '研究A' })).toBeVisible()
  await page.getByRole('button', { name: '查看', exact: true }).click()
  await expect(page.getByText('B研究报告', { exact: true })).toBeVisible()
  await page.clock.runFor(2100)
  await expect(page.getByText('B研究报告', { exact: true })).toBeVisible()
})

test('late research history response cannot replace the current selection', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [task('A', 'completed'), task('B', 'completed')] } }))
  await page.route('**/api/v1/research/*/status', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!
    if (id === 'A') await gate
    await route.fulfill({ json: task(id, 'completed') })
  })
  await page.goto('/research')
  const rows = page.locator('.ant-list-item')
  await rows.filter({ hasText: '研究A' }).getByRole('button', { name: '查看', exact: true }).click()
  await rows.filter({ hasText: '研究B' }).getByRole('button', { name: '查看', exact: true }).click()
  await expect(page.getByText('B研究报告', { exact: true })).toBeVisible()
  const response = page.waitForResponse('**/api/v1/research/A/status')
  release()
  await response
  await expect(page.getByText('A研究报告', { exact: true })).toBeHidden()
  await expect(page.getByText('B研究报告', { exact: true })).toBeVisible()
})

test('research status request failure is visible and does not poll forever', async ({ page }) => {
  await page.clock.install()
  let requests = 0
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [] } }))
  await page.route('**/api/v1/research/start', route => route.fulfill({ json: task('A') }))
  await page.route('**/api/v1/research/A/status', route => {
    requests++
    return route.fulfill({ status: 404, json: { detail: '任务不存在' } })
  })
  await page.goto('/research')
  await page.getByPlaceholder(/例如：大语言模型/).fill('状态故障研究')
  await page.getByRole('button', { name: '开始研究' }).click()
  await page.clock.runFor(2100)
  await expect(page.getByText(/获取任务状态失败/)).toBeVisible()
  const before = requests
  await page.clock.runFor(6000)
  expect(requests).toBe(before)
})

test('viewing completed book stops previous compilation polling', async ({ page }) => {
  await page.clock.install()
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: [book('A'), book('B', 'completed')] } }))
  await page.route('**/api/v1/books/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!
    return route.fulfill({ json: book(id, id === 'B' ? 'completed' : 'compiling') })
  })
  await page.goto('/books')
  await page.getByText('教材A', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材A' })).toBeVisible()
  await page.getByText('教材B', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材B' })).toBeVisible()
  await page.clock.runFor(3200)
  await expect(page.getByRole('heading', { name: '教材B' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导出 Markdown' })).toBeVisible()
})

test('late book response cannot replace the current selection', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: [book('A'), book('B', 'completed')] } }))
  await page.route('**/api/v1/books/*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!
    if (id === 'A') await gate
    await route.fulfill({ json: book(id, 'completed') })
  })
  await page.goto('/books')
  await page.getByText('教材A', { exact: true }).click()
  await page.getByText('教材B', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材B' })).toBeVisible()
  const response = page.waitForResponse('**/api/v1/books/A')
  release()
  await response
  await expect(page.getByRole('heading', { name: '教材A' })).toBeHidden()
  await expect(page.getByRole('heading', { name: '教材B' })).toBeVisible()
})

test('failed book shows its cause and cannot be exported', async ({ page }) => {
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: [book('故障', 'error')] } }))
  await page.route('**/api/v1/books/*', route => route.fulfill({ json: { ...book('故障', 'error'), error: '受控模型未返回教材正文' } }))
  await page.goto('/books')
  await page.getByText('教材故障', { exact: true }).click()
  await expect(page.getByText('受控模型未返回教材正文', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '导出 Markdown' })).toBeHidden()
})

test('starting research twice leaves only the new task polling', async ({ page }) => {
  await page.clock.install()
  let next = 0
  const polled: string[] = []
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [] } }))
  await page.route('**/api/v1/research/start', route => route.fulfill({ json: task(++next === 1 ? 'A' : 'B') }))
  await page.route('**/api/v1/research/*/status', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!
    polled.push(id)
    return route.fulfill({ json: task(id) })
  })
  await page.goto('/research')
  await page.getByPlaceholder(/例如：大语言模型/).fill('第一项研究')
  await page.getByRole('button', { name: '开始研究' }).click()
  await expect(page.locator('.ant-card-head-title').filter({ hasText: '研究A' })).toBeVisible()
  await page.getByPlaceholder(/例如：大语言模型/).fill('第二项研究')
  await page.getByRole('button', { name: '开始研究' }).click()
  await expect(page.locator('.ant-card-head-title').filter({ hasText: '研究B' })).toBeVisible()
  await page.clock.runFor(2200)
  await expect.poll(() => polled.length).toBeGreaterThan(0)
  expect(polled.every(id => id === 'B')).toBe(true)
})

test('book polling failure is visible and stops repeated requests', async ({ page }) => {
  await page.clock.install()
  let calls = 0
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: [book('A')] } }))
  await page.route('**/api/v1/books/A', route => {
    calls++
    return calls === 1 ? route.fulfill({ json: book('A') }) : route.fulfill({ status: 503, json: { detail: '临时不可用' } })
  })
  await page.goto('/books')
  await page.getByText('教材A', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材A' })).toBeVisible()
  await page.clock.runFor(3200)
  await expect(page.getByText(/获取教材状态失败/)).toBeVisible()
  const before = calls
  await page.clock.runFor(9000)
  expect(calls).toBe(before)
})

test('late deletion of old book does not clear the newly selected book', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let deleted = false
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: deleted ? [book('B', 'completed')] : [book('A', 'completed'), book('B', 'completed')] } }))
  await page.route('**/api/v1/books/*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!
    if (route.request().method() === 'DELETE') {
      await gate
      deleted = true
      return route.fulfill({ json: { status: 'ok' } })
    }
    return route.fulfill({ json: book(id, 'completed') })
  })
  await page.goto('/books')
  await page.getByText('教材A', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材A' })).toBeVisible()
  await page.locator('.ant-list-item').filter({ hasText: '教材A' }).getByRole('button').click()
  await page.getByRole('button', { name: /确\s*定/ }).click()
  await page.getByRole('button', { name: /取\s*消/ }).click()
  await page.getByText('教材B', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材B' })).toBeVisible()
  const response = page.waitForResponse('**/api/v1/books/A')
  release()
  await response
  await expect(page.getByText('已删除', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '教材B' })).toBeVisible()
})

test('book accepted before detail failure remains available with truthful feedback', async ({ page }) => {
  let created = false
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: created ? [book('new', 'planning')] : [] } }))
  await page.route('**/api/v1/books/create', route => {
    created = true
    return route.fulfill({ json: book('new', 'planning') })
  })
  await page.route('**/api/v1/books/new', route => route.fulfill({ status: 503, json: { detail: '详情临时不可用' } }))
  await page.goto('/books')
  await page.getByRole('button', { name: /创建教材/ }).click()
  await page.getByPlaceholder(/教材标题（例如/).fill('新教材')
  await page.getByRole('button', { name: '开始编译', exact: true }).click()
  await expect(page.getByText(/教材已创建，获取详情失败/)).toBeVisible()
  await expect(page.locator('.ant-list-item').filter({ hasText: '教材new' })).toBeVisible()
})

test('research completion displays report and sources and stops polling', async ({ page }) => {
  await page.clock.install()
  let calls = 0
  await page.route('**/api/v1/research/tasks', route => route.fulfill({ json: { tasks: [] } }))
  await page.route('**/api/v1/research/start', route => route.fulfill({ json: task('A') }))
  await page.route('**/api/v1/research/A/status', route => {
    calls++
    return route.fulfill({ json: { ...task('A', 'completed'), sources: [{ title: '明确模拟来源', url: 'https://example.invalid/reference', source: 'web' }] } })
  })
  await page.goto('/research')
  await page.getByPlaceholder(/例如：大语言模型/).fill('完整研究流程')
  await page.getByRole('button', { name: '开始研究' }).click()
  await expect(page.locator('.ant-card-head-title').filter({ hasText: '研究A' })).toBeVisible()
  await page.clock.runFor(2200)
  await expect(page.getByText('A研究报告', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '明确模拟来源' })).toHaveAttribute('href', 'https://example.invalid/reference')
  const before = calls
  await page.clock.runFor(6000)
  expect(calls).toBe(before)
})

test('created book compiles content and exports markdown', async ({ page }) => {
  await page.clock.install()
  let created = false
  let gets = 0
  const completed = { ...book('new', 'completed'), chapters: [{ id: 'chapter', title: '能量章节', sections: [{ id: 'section', title: '能量转换', blocks: [{ id: 'text', type: 'text', content: '受控教材正文', order_index: 0 }], order_index: 0 }], order_index: 0 }] }
  await page.route('**/api/v1/books', route => route.fulfill({ json: { books: created ? [book('new')] : [] } }))
  await page.route('**/api/v1/books/create', route => { created = true; return route.fulfill({ json: book('new', 'planning') }) })
  await page.route('**/api/v1/books/new', route => route.fulfill({ json: ++gets === 1 ? book('new') : completed }))
  await page.route('**/api/v1/books/new/export', route => route.fulfill({ contentType: 'text/markdown', body: '# 教材new\n受控教材正文' }))
  await page.goto('/books')
  await page.getByRole('button', { name: /创建教材/ }).click()
  await page.getByPlaceholder(/教材标题（例如/).fill('新教材')
  await page.getByRole('button', { name: '开始编译', exact: true }).click()
  await expect(page.getByRole('heading', { name: '教材new' })).toBeVisible()
  await page.clock.runFor(3200)
  await expect(page.getByText('受控教材正文', { exact: true })).toBeVisible()
  const before = gets
  await page.clock.runFor(9000)
  expect(gets).toBe(before)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出 Markdown' }).click()
  expect((await download).suggestedFilename()).toBe('教材new.md')
})
