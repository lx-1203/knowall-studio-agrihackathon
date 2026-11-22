import { test, expect } from './fixtures'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/documents/**', route => {
    const path = new URL(route.request().url()).pathname
    const doc = { id: 'bio', filename: '生物教材.md', file_type: 'md', status: 'ready', subject: '生物', material_kind: 'knowledge' }
    if (path.endsWith('/chunks')) return route.fulfill({ json: [
      { id: 'c1', index: 0, text: '# 第一章 细胞\n细胞结构。' },
      { id: 'c2', index: 1, text: '# 第二章 遗传\nDNA 保存遗传信息。' },
    ] })
    if (path.endsWith('/raw')) return route.fulfill({ contentType: 'text/plain', body: '# 教材原文件\n可以直接阅读。' })
    return route.fulfill({ json: path.endsWith('/documents/') ? [doc] : doc })
  })
  await page.route('**/api/v1/learning/**', route => route.fulfill({ json: null }))
})

test('reading resumes its chapter and mode after reload and library revisit', async ({ page }) => {
  await page.goto('/materials/bio')
  await page.getByRole('button', { name: /第二章 遗传/ }).click()
  await page.reload()
  await expect(page.getByRole('article', { name: '资料正文' })).toContainText('DNA 保存遗传信息。')
  await page.locator('.material-back').click()
  await expect(page.getByTestId('material-continue')).toContainText('第二章 遗传')
  await page.getByTestId('material-continue').getByRole('link', { name: '继续阅读' }).click()
  await expect(page.getByRole('article', { name: '资料正文' })).toContainText('DNA 保存遗传信息。')
})

test('library subject and type filters survive opening and returning', async ({ page }) => {
  await page.goto('/materials?subject=%E7%94%9F%E7%89%A9&kind=knowledge&q=%E6%95%99%E6%9D%90')
  await page.getByRole('link', { name: '打开 生物教材.md' }).click()
  await page.locator('.material-back').click()
  await expect(page.getByRole('textbox', { name: '搜索我的资料' })).toHaveValue('教材')
  await expect(page.getByRole('button', { name: '科目 生物', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('[aria-label="资料类型"]').getByRole('radio', { name: '知识资料', exact: true })).toBeChecked()
})

test('notes save automatically before immediately leaving the material', async ({ page }) => {
  await page.goto('/materials/bio')
  await page.getByRole('textbox', { name: '我的学习笔记' }).fill('无需按保存，也不会丢失。')
  await page.locator('.material-back').click()
  await page.getByRole('link', { name: '打开 生物教材.md' }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('无需按保存，也不会丢失。')
})

test('compact tools open next to reading and close on locating original text', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/materials/bio')
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toBeVisible()
  await page.getByRole('textbox', { name: '我的学习笔记' }).fill('手机上的随手笔记')
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(page.getByText('可以直接阅读。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '学习工具', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('手机上的随手笔记')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

function samplePdf() {
  const records = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> /Contents 5 0 R >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> /Contents 5 0 R >>',
    '<< /Length 0 >>\nstream\n\nendstream',
  ]
  let pdf = '%PDF-1.4\n'
  const offsets = [0]
  records.forEach((record, index) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${index + 1} 0 obj\n${record}\nendobj\n` })
  const xref = Buffer.byteLength(pdf)
  pdf += `xref\n0 ${records.length + 1}\n0000000000 65535 f \n`
  pdf += offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
  pdf += `trailer\n<< /Size ${records.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`
  return Buffer.from(pdf)
}

test('PDF fits mobile width and restores the actual page on reopening', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**/api/v1/documents/pdf/**', route => {
    if (route.request().url().endsWith('/raw')) return route.fulfill({ contentType: 'application/pdf', body: samplePdf() })
    return route.fulfill({ json: [] })
  })
  await page.route('**/api/v1/documents/pdf', route => route.fulfill({ json: { id: 'pdf', filename: '教材.pdf', file_type: 'pdf', status: 'ready', subject: '生物', material_kind: 'knowledge', page_count: 2 } }))
  await page.goto('/materials/pdf')
  await expect(page.locator('.react-pdf__Page canvas')).toBeVisible()
  const size = await page.locator('.react-pdf__Page canvas').evaluate(element => element.getBoundingClientRect().width)
  expect(size).toBeLessThan(345)
  await page.getByRole('button', { name: 'PDF 下一页', exact: true }).click()
  await expect(page.getByRole('spinbutton', { name: 'PDF 页码', exact: true })).toHaveValue('2')
  await page.reload()
  await expect(page.getByRole('spinbutton', { name: 'PDF 页码', exact: true })).toHaveValue('2')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

test('failed upload keeps classification and offers a visible correction', async ({ page }) => {
  await page.route('**/api/v1/documents/upload', route => route.fulfill({ status: 400, json: { detail: '文件格式不支持，请选择 PDF 或 Markdown。' } }))
  await page.goto('/materials')
  await page.getByRole('button', { name: /上传资料$/ }).click()
  await page.getByRole('textbox', { name: '上传资料科目' }).fill('生物')
  await page.locator('input[type="file"]').setInputFiles({ name: '教材.md', mimeType: 'text/markdown', buffer: Buffer.from('cells') })
  await expect(page.getByRole('alert').getByText('文件格式不支持，请选择 PDF 或 Markdown。')).toBeVisible()
  await expect(page.getByRole('textbox', { name: '上传资料科目' })).toHaveValue('生物')
})

test('invalid reading history does not prevent opening a material', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('knowall:material-position:e2e-user:bio', '{bad-json'))
  await page.goto('/materials/bio')
  await expect(page.getByText('可以直接阅读。', { exact: true })).toBeVisible()
})

test('continue reading respects the active subject rather than showing unrelated history', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('knowall:material-position:e2e-user:history', JSON.stringify({ chunkIndex: 0, mode: 'text', chapter: '历史复习', updatedAt: Date.now() })))
  await page.route(/\/api\/v1\/documents\/(?:\?.*)?$/, route => route.fulfill({ json: [
    { id: 'bio', filename: '生物教材.md', subject: '生物', material_kind: 'knowledge', file_type: 'md', status: 'ready' },
    { id: 'history', filename: '历史笔记.md', subject: '历史', material_kind: 'knowledge', file_type: 'md', status: 'ready' },
  ] }))
  await page.goto('/materials?subject=%E7%94%9F%E7%89%A9')
  await expect(page.getByRole('link', { name: '打开 生物教材.md' })).toBeVisible()
  await expect(page.getByTestId('material-continue')).toHaveCount(0)
})

test('related sources connect a subject bank without mixing history or generated content', async ({ page }) => {
  await page.route(/\/api\/v1\/documents\/(?:\?.*)?$/, route => route.fulfill({ json: [
    { id: 'bio', filename: '生物教材.md', subject: '生物', material_kind: 'knowledge', file_type: 'md' },
    { id: 'bank', filename: '生物试卷.md', subject: '生物', material_kind: 'question_bank', file_type: 'md' },
    { id: 'history', filename: '历史.md', subject: '历史', material_kind: 'knowledge', file_type: 'md' },
  ] }))
  await page.route('**/api/v1/documents/bank', route => route.fulfill({ json: { id: 'bank', filename: '生物试卷.md', subject: '生物', material_kind: 'question_bank', file_type: 'md', status: 'ready' } }))
  await page.route('**/api/v1/documents/bank/raw', route => route.fulfill({ contentType: 'text/plain', body: '# 原始生物试卷\n一道上传的原始题目。' }))
  await page.goto('/materials/bio')
  await page.getByText('同科目资料', { exact: true }).click()
  const related = page.getByRole('region', { name: '同科目资料', exact: true })
  await expect(related.getByRole('link', { name: '打开相关资料 生物试卷.md', exact: true })).toBeVisible()
  await expect(related.getByText('历史.md', { exact: true })).toHaveCount(0)
  await related.getByRole('link', { name: '打开相关资料 生物试卷.md', exact: true }).click()
  await expect(page.getByRole('heading', { name: '生物试卷.md', exact: true })).toBeVisible()
  await expect(page.getByText('一道上传的原始题目。', { exact: true })).toBeVisible()
})

test('focus reading enlarges the source and brings tools back without losing notes', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto('/materials/bio')
  await page.getByRole('textbox', { name: '我的学习笔记' }).fill('专注模式保留笔记')
  await page.getByRole('button', { name: '专注阅读', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '当前资料学习工具' })).toBeHidden()
  await page.getByRole('button', { name: '结束专注', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '我的学习笔记' })).toHaveValue('专注模式保留笔记')
})
