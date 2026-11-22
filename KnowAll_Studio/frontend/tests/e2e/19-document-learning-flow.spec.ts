import { test, expect } from './fixtures'
import { readFile } from 'node:fs/promises'

test('file upload continues into generated practice, source, reload and download', async ({ page }) => {
  let uploaded = false
  let generated = false
  let answered = false
  const document = { id: 'flow-doc', filename: 'biology.txt', file_type: 'txt', status: 'ready', total_chunks: 1 }
  const question = { id: 'flow-question', question_type: 'single_choice', question_text: '哪种细胞器提供能量？', options: [{ label: 'A', text: '线粒体' }, { label: 'B', text: '细胞壁' }] }
  const pack = { id: 'flow-pack', title: 'biology.txt 学习包', stale: false, coverage: { total_sections: 1, question_sections: 1, flashcard_sections: 0 }, quality: { total: 1, blocked: 0 }, sections: [{ id: 'section', title: '细胞能量', notes: '线粒体进行细胞呼吸。', chunk_ids: ['flow-chunk'], page_ranges: ['p1'], errors: [], questions: [{ ...question, answer: 'A', analysis: '依据原文', quality: { eligible: true } }], flashcards: [] }] }
  await page.route('**/api/v1/documents/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/upload')) {
      expect(route.request().headers()['content-type']).toContain('multipart/form-data')
      expect(route.request().postDataBuffer()?.toString()).toContain('Mitochondria produce energy')
      uploaded = true
      return route.fulfill({ json: { ...document, document_id: document.id } })
    }
    if (path.endsWith('/chunks')) return route.fulfill({ json: [{ id: 'flow-chunk', text: '线粒体进行细胞呼吸并提供能量。' }] })
    if (path.endsWith('/index')) return route.fulfill({ json: { status: 'ready' } })
    return route.fulfill({ json: uploaded ? [document] : [] })
  })
  await page.route('**/api/v1/learning/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/generate')) {
      expect(route.request().postDataJSON().document_id).toBe(document.id)
      generated = true
      return route.fulfill({ json: pack })
    }
    if (url.pathname.endsWith('/session')) return route.fulfill({ json: { items: [{ kind: 'question', reason: 'practice', title: '细胞能量', notes: '线粒体进行细胞呼吸。', question, source: { chunk_ids: ['flow-chunk'], page_ranges: ['p1'] } }] } })
    if (url.pathname.endsWith('/answers')) {
      expect(route.request().postDataJSON()).toMatchObject({ question_id: question.id, answer: 'A' })
      answered = true
      return route.fulfill({ json: { is_correct: true, correct_answer: 'A', analysis: '依据原文', score_ratio: 1 } })
    }
    if (url.pathname.endsWith('/export')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(pack) })
    return route.fulfill({ json: url.pathname.endsWith('/pack') ? (generated ? pack : null) : { sections: [] } })
  })
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: 'biology.txt', mimeType: 'text/plain', buffer: Buffer.from('Mitochondria produce energy') })
  await expect(page.getByText(/上传成功: biology.txt/)).toBeVisible()
  expect(uploaded).toBeTruthy()
  await page.getByRole('navigation', { name: '学习导航' }).getByText('更多工具', { exact: true }).click()
  await page.getByRole('navigation', { name: '学习导航' }).getByRole('link', { name: '内容整理与质检', exact: true }).click()
  await page.getByRole('button', { name: '生成学习包', exact: true }).click()
  await expect(page.getByText(pack.title, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '章节练习', exact: true }).click()
  await page.getByRole('radio', { name: /线粒体/ }).check()
  await page.getByRole('button', { name: '提交答案' }).click()
  await expect(page.getByText('回答正确', { exact: true })).toBeVisible()
  expect(answered).toBeTruthy()
  await page.getByRole('button', { name: '定位来源原文' }).click()
  await expect(page.getByText('线粒体进行细胞呼吸并提供能量。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Close', exact: true }).click()
  await page.reload()
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('biology.txt', { exact: true }).click()
  await expect(page.getByText(pack.title, { exact: true })).toBeVisible()
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 JSON', exact: true }).click()
  const download = await downloadEvent
  expect(JSON.parse(await readFile((await download.path())!, 'utf8')).id).toBe(pack.id)
})

test('failed recognition remains an error and permits another upload', async ({ page }) => {
  let attempts = 0
  await page.route('**/api/v1/documents/upload', route => {
    attempts++
    return route.fulfill({ status: 400, json: { detail: '未识别到可读取的文字' } })
  })
  await page.goto('/upload')
  const input = page.locator('input[type=file]')
  await input.setInputFiles({ name: 'empty.txt', mimeType: 'text/plain', buffer: Buffer.alloc(0) })
  await expect(page.getByText('上传失败: 未识别到可读取的文字')).toBeVisible()
  await expect(page.getByText(/上传成功:/)).toHaveCount(0)
  await input.setInputFiles({ name: 'retry.txt', mimeType: 'text/plain', buffer: Buffer.from('retry') })
  await expect.poll(() => attempts).toBe(2)
})

test('all section generation failures never announce a successful learning pack', async ({ page }) => {
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: [{ id: 'failed-doc', filename: 'biology.txt' }] }))
  await page.route('**/api/v1/learning/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/generate')) return route.fulfill({ json: {
      id: 'failed-pack', title: 'biology.txt', coverage: { total_sections: 1, failed_sections: 1 }, quality: { blocked: 0 },
      sections: [{ id: 'failed-section', title: '细胞能量', notes: '', errors: ['本章生成失败，可重新生成'], questions: [], flashcards: [] }],
    } })
    return route.fulfill({ json: path.endsWith('/pack') ? null : { sections: [] } })
  })
  await page.goto('/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('biology.txt', { exact: true }).click()
  await page.getByRole('button', { name: '生成学习包', exact: true }).click()
  await expect(page.getByText('所有章节生成失败，请检查模型配置后重试。', { exact: true })).toBeVisible()
  await expect(page.getByText('学习包已生成', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /生成学习包$/ })).toBeEnabled()
})
