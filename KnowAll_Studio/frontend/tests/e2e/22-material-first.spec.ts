import { test, expect } from './fixtures'

const biology = { id: 'bio', filename: '生物教材.md', file_type: 'md', status: 'ready', subject: '生物', material_kind: 'knowledge', page_count: 12 }
const history = { id: 'history', filename: '历史笔记.md', file_type: 'md', status: 'ready', subject: '历史', material_kind: 'knowledge' }
const bank = { id: 'bank', filename: '生物试卷.md', file_type: 'md', status: 'ready', subject: '生物', material_kind: 'question_bank' }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/documents/**', route => {
    const path = new URL(route.request().url()).pathname
    const doc = path.includes('/history') ? history : path.includes('/bank') ? bank : biology
    if (path.endsWith('/chunks')) return route.fulfill({ json: [
      { id: `${doc.id}-c1`, index: 0, text: '# 第一章 细胞\n细胞是生命活动的基本单位。', page_range: 'p1' },
      { id: `${doc.id}-c2`, index: 1, text: '# 第二章 遗传\nDNA 保存遗传信息。', page_range: 'p2' },
    ] })
    if (path.endsWith('/raw')) return route.fulfill({ contentType: 'text/plain', body: '# 原始教材\n这段原文无需生成即可阅读。' })
    if (path.endsWith('/organization')) return route.fulfill({ json: { ...doc, ...route.request().postDataJSON() } })
    return route.fulfill({ json: path.endsWith('/documents/') ? [biology, history, bank] : doc })
  })
  await page.route('**/api/v1/learning/**', route => route.fulfill({ json: null }))
})

test('home is a material library and filters subjects and source types', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '打开 生物教材.md' })).toBeVisible()
  await page.getByRole('button', { name: '科目 生物', exact: true }).click()
  await expect(page.getByRole('link', { name: '打开 历史笔记.md' })).toHaveCount(0)
  await page.locator('[aria-label="资料类型"]').getByText('题库', { exact: true }).click()
  await expect(page.getByRole('link', { name: '打开 生物试卷.md' })).toBeVisible()
  await expect(page.getByRole('link', { name: '打开 生物教材.md' })).toHaveCount(0)
  await expect(page.getByRole('navigation').getByRole('link', { name: '分子生物学刷题', exact: true })).toHaveCount(0)
})

test('opening a source reads immediately and survives reload without generation', async ({ page }) => {
  const writes: string[] = []
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()) })
  await page.goto('/')
  await page.getByRole('link', { name: '打开 生物教材.md' }).click()
  await expect(page).toHaveURL(/\/materials\/bio$/)
  await expect(page.getByText('这段原文无需生成即可阅读。', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '生物教材.md', exact: true })).toBeVisible()
  await expect(page.getByText('这段原文无需生成即可阅读。', { exact: true })).toBeVisible()
  expect(writes.filter(url => url.includes('/learning/generate'))).toHaveLength(0)
  await page.getByRole('button', { name: /第二章 遗传/ }).click()
  await expect(page.getByRole('article', { name: '资料正文' }).getByText('DNA 保存遗传信息。')).toBeVisible()
})

test('notebook persists for the current account and document only', async ({ page }) => {
  await page.goto('/materials/bio')
  await page.getByRole('textbox', { name: '我的学习笔记' }).fill('生物：复习细胞结构')
  await page.getByRole('button', { name: '保存笔记', exact: true }).click()
  await page.reload()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('生物：复习细胞结构')
  await page.goto('/materials/history')
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('')
})

test('study tools generate only for the URL material and locate the source', async ({ page }) => {
  const question = { id: 'q1', question_type: 'single_choice', question_text: '遗传信息存放在哪里？', options: [{ label: 'A', text: 'DNA' }, { label: 'B', text: '细胞膜' }] }
  let generated = false
  const pack = { id: 'bio-pack', title: '生物学习包', stale: false, config: { question_count: 10, card_count: 10 }, quality: { blocked: 0 }, coverage: { total_sections: 1, failed_sections: 0 }, sections: [{ id: 's1', title: '遗传', notes: '遗传信息由 DNA 保存。', chunk_ids: ['bio-c2'], page_ranges: ['p2'], questions: [{ ...question, answer: 'A', quality: { eligible: true } }], flashcards: [{ id: 'f1', front: '遗传信息由什么保存？', back: 'DNA' }], errors: [] }] }
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/generate')) { expect(route.request().postDataJSON().document_id).toBe('bio'); generated = true; return route.fulfill({ json: pack }) }
    if (path.endsWith('/session')) return route.fulfill({ json: { items: [{ kind: 'question', question, reason: 'practice', title: '遗传', notes: 'DNA 保存遗传信息。', source: { chunk_ids: ['bio-c2'], page_ranges: ['p2'] } }] } })
    return route.fulfill({ json: generated ? pack : null })
  })
  await page.goto('/materials/bio')
  await page.getByRole('button', { name: '整理学习内容', exact: true }).click()
  await page.getByRole('button', { name: '生成学习包', exact: true }).click()
  await page.getByRole('tab', { name: '练习', exact: true }).click()
  await page.getByRole('button', { name: '章节练习', exact: true }).click()
  await expect(page.getByText(question.question_text, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '定位来源原文', exact: true }).click()
  await expect(page.getByRole('article', { name: '资料正文' }).getByText('DNA 保存遗传信息。')).toBeVisible()
  await page.getByRole('tab', { name: '知识卡', exact: true }).click()
  await expect(page.getByText('遗传信息由什么保存？', { exact: true })).toBeVisible()
  await expect(page.getByTestId('material-card-answer')).toHaveCount(0)
  await page.getByRole('button', { name: '显示答案', exact: true }).click()
  await expect(page.getByTestId('material-card-answer')).toHaveText('DNA')
})

test('small screens retain source and tools without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/materials/bio')
  await expect(page.getByText('这段原文无需生成即可阅读。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

test('original source requests carry the signed-in account token', async ({ page }) => {
  let authorization = ''
  await page.route('**/api/v1/documents/bio/raw', route => {
    authorization = route.request().headers().authorization || ''
    return route.fulfill({ contentType: 'text/plain', body: '# 登录资料\n原文件内容。' })
  })
  await page.goto('/materials/bio')
  await expect(page.getByText('原文件内容。', { exact: true })).toBeVisible()
  expect(authorization).toBe('Bearer e2e-token-not-a-real-jwt')
})

test('upload classifies the source and opens it for reading immediately', async ({ page }) => {
  await page.route('**/api/v1/documents/upload', route => {
    expect(route.request().postDataBuffer()?.toString()).toContain('DNA stores information')
    return route.fulfill({ json: { document_id: 'bio', filename: biology.filename, status: 'ready' } })
  })
  let classified = false
  await page.route('**/api/v1/documents/bio/organization', route => {
    expect(route.request().postDataJSON()).toEqual({ subject: '生物', material_kind: 'knowledge' })
    classified = true
    return route.fulfill({ json: biology })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /上传资料$/ }).click()
  await page.getByRole('textbox', { name: '上传资料科目' }).fill('生物')
  await page.locator('input[type=file]').setInputFiles({ name: '生物教材.md', mimeType: 'text/markdown', buffer: Buffer.from('DNA stores information') })
  await expect(page).toHaveURL(/\/materials\/bio$/)
  await expect(page.getByText('这段原文无需生成即可阅读。', { exact: true })).toBeVisible()
  expect(classified).toBeTruthy()
})

test('material classification changes both filtering and the server record', async ({ page }) => {
  let saved = false
  await page.route('**/api/v1/documents/bio/organization', route => {
    expect(route.request().postDataJSON()).toEqual({ subject: '医学', material_kind: 'knowledge' })
    saved = true
    return route.fulfill({ json: { ...biology, subject: '医学' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '管理 生物教材.md' }).click()
  await page.getByRole('menuitem', { name: '修改科目与类型' }).click()
  await page.getByRole('textbox', { name: '资料科目', exact: true }).fill('医学')
  await page.getByRole('button', { name: '保存分类', exact: true }).click()
  await page.getByRole('button', { name: '科目 医学', exact: true }).click()
  await expect(page.getByRole('link', { name: '打开 生物教材.md' })).toBeVisible()
  await expect(page.getByRole('link', { name: '打开 生物试卷.md' })).toHaveCount(0)
  expect(saved).toBeTruthy()
})

test('a failed original preview still allows reading the parsed source', async ({ page }) => {
  await page.route('**/api/v1/documents/bio/raw', route => route.fulfill({ status: 503, body: 'unavailable' }))
  await page.goto('/materials/bio')
  await expect(page.getByRole('button', { name: '重新加载原文件' })).toBeVisible()
  await page.getByRole('button', { name: /第一章 细胞/ }).click()
  await expect(page.getByRole('article', { name: '资料正文' }).getByText('细胞是生命活动的基本单位。')).toBeVisible()
})

test('material questions use the selected source passage instead of all subjects', async ({ page }) => {
  await page.route('**/api/v1/chat/assistant', route => {
    expect(route.request().postDataJSON().knowledge_context).toBe('# 第二章 遗传\nDNA 保存遗传信息。')
    return route.fulfill({ json: { message: '这一段说明 DNA 保存遗传信息。' } })
  })
  await page.goto('/materials/bio')
  await page.getByRole('button', { name: /第二章 遗传/ }).click()
  await page.getByRole('tab', { name: '问答', exact: true }).click()
  await page.getByRole('textbox', { name: '向当前资料提问' }).fill('解释一下')
  await page.getByRole('button', { name: /发送问题$/ }).click()
  await expect(page.getByText('这一段说明 DNA 保存遗传信息。', { exact: true })).toBeVisible()
})

test('current design screenshots cover library, study, mobile and dark theme', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto('/')
  await expect(page.getByRole('link', { name: '打开 生物教材.md' })).toBeVisible()
  await page.screenshot({ path: '.tmp/material-library-desktop.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('link', { name: '打开 生物教材.md' }).click()
  await expect(page.getByText('这段原文无需生成即可阅读。', { exact: true })).toBeVisible()
  await page.screenshot({ path: '.tmp/material-study-desktop.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await page.screenshot({ path: '.tmp/material-study-dark.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '切换到浅色模式' }).click()
  await page.setViewportSize({ width: 375, height: 812 })
  await page.screenshot({ path: '.tmp/material-study-mobile.png', fullPage: true, animations: 'disabled' })
  expect(errors).toEqual([])
})

test('PPT source reads protected slide images with the account token', async ({ page }) => {
  const authorized: string[] = []
  await page.route('**/api/v1/documents/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/slides')) { authorized.push(route.request().headers().authorization || ''); return route.fulfill({ json: { slides: [{ index: 0, image_url: '/api/v1/documents/presentation/slides/0/image' }] } }) }
    if (path.endsWith('/image')) { authorized.push(route.request().headers().authorization || ''); return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==', 'base64') }) }
    if (path.endsWith('/chunks')) return route.fulfill({ json: [] })
    return route.fulfill({ json: { ...biology, id: 'presentation', filename: '课件.pptx', file_type: 'pptx' } })
  })
  await page.goto('/materials/presentation')
  const image = page.getByRole('img', { name: '幻灯片 1', exact: true })
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(1)
  expect(authorized).toEqual(['Bearer e2e-token-not-a-real-jwt', 'Bearer e2e-token-not-a-real-jwt'])
})

test('notes from one account do not appear for another account on the same source', async ({ page }) => {
  await page.goto('/materials/bio')
  await page.getByRole('textbox', { name: '我的学习笔记' }).fill('第一位同学的笔记')
  await page.getByRole('button', { name: '保存笔记', exact: true }).click()
  await page.addInitScript(() => localStorage.setItem('knowall_user', JSON.stringify({ id: 'another-student', username: '另一位同学' })))
  await page.reload()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('')
})

test('keyboard users can activate a chapter and return to the library', async ({ page }) => {
  await page.goto('/materials/bio')
  const chapter = page.getByRole('button', { name: /第二章 遗传/ })
  await chapter.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('article', { name: '资料正文' }).getByText('DNA 保存遗传信息。')).toBeVisible()
  await page.getByRole('link', { name: /我的资料$/ }).last().focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '我的资料', exact: true })).toBeVisible()
})
