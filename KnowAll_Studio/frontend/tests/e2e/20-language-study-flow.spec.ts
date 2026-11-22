import { test, expect } from './fixtures'

test('backend words response renders and mastery survives a reload', async ({ page }) => {
  let mastered = false
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: { id: 'biology', filename: 'biology.txt' } }))
  await page.route('**/api/v1/language/vocabulary**', route => {
    if (route.request().method() === 'PATCH') {
      mastered = route.request().postDataJSON().mastered
      return route.fulfill({ json: { id: 'energy', mastered } })
    }
    return route.fulfill({ json: { total: 1, words: [{ id: 'energy', word: 'energy', definition: '能量', part_of_speech: 'noun', difficulty: 'easy', mastered }] } })
  })
  await page.goto('/language?docId=biology')
  await expect(page.getByText('energy', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /标记已掌握/ }).click()
  await expect.poll(() => mastered).toBe(true)
  await page.reload()
  await expect(page.getByRole('button', { name: /已掌握/ })).toBeVisible()
})

test('create passage, retry failed dictation, rate and reload saved passage', async ({ page }) => {
  const content = '床前明月光，疑是地上霜。'
  let saved = false
  let submissions = 0
  let rated = false
  const passage = { id: 'poem', title: '静夜思', content, category: 'custom', difficulty: 1, created_at: new Date(0).toISOString() }
  await page.route('**/api/v1/recitation/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/passages')) {
      if (route.request().method() === 'POST') { expect(route.request().postDataJSON().content).toBe(content); saved = true; return route.fulfill({ json: passage }) }
      return route.fulfill({ json: { passages: saved ? [passage] : [], total: saved ? 1 : 0 } })
    }
    if (path.endsWith('/dictation/submit')) {
      submissions++
      expect(route.request().postDataJSON().user_input).toBe(content)
      if (submissions === 1) return route.fulfill({ status: 503, json: { detail: 'temporary unavailable' } })
      return route.fulfill({ json: { record_id: 'record', accuracy: 1, diffs: [{ type: 'equal', text: content }], original_length: content.length, input_length: content.length, correct_chars: content.length, missing_chars: 0, extra_chars: 0 } })
    }
    if (path.endsWith('/review')) { expect(route.request().postDataJSON()).toEqual({ record_id: 'record', rating: 3 }); rated = true; return route.fulfill({ json: { review_count: 1 } }) }
    if (path.endsWith('/stats')) return route.fulfill({ json: { total_passages: saved ? 1 : 0, total_records: submissions > 1 ? 1 : 0, avg_accuracy: submissions > 1 ? 1 : 0, mastered: 0 } })
    return route.fulfill({ json: { due_items: [] } })
  })
  await page.goto('/recitation')
  await page.getByRole('button', { name: /新建段落/ }).click()
  await page.getByLabel('标题', { exact: true }).fill(passage.title)
  await page.getByLabel('原文内容', { exact: true }).fill(content)
  await page.getByRole('button', { name: /^创\s*建$/ }).click()
  await expect(page.getByText(passage.title, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /默写/ }).click()
  await page.getByPlaceholder('在此输入你的默写内容...').fill(content)
  await page.getByRole('button', { name: /^检\s*查$/ }).click()
  await expect(page.getByText('提交失败', { exact: true })).toBeVisible()
  await expect(page.getByPlaceholder('在此输入你的默写内容...')).toHaveValue(content)
  await page.getByRole('button', { name: /检\s*查$/ }).click()
  await page.getByRole('button', { name: /记住了/ }).click()
  await expect.poll(() => rated).toBe(true)
  await page.reload()
  await expect(page.getByText(passage.title, { exact: true })).toBeVisible()
})

test('article upload failure keeps title and content for retry', async ({ page }) => {
  await page.route('**/api/v1/reading/articles/upload', route => route.fulfill({ status: 400, json: { detail: '文章太短' } }))
  await page.goto('/reading')
  await page.getByRole('button', { name: /上传文章/ }).click()
  await page.getByPlaceholder('文章标题（必填）').fill('练习文章')
  await page.getByPlaceholder('文章正文...').fill('短文章')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('上传失败: 文章太短', { exact: true })).toBeVisible()
  await expect(page.getByPlaceholder('文章标题（必填）')).toHaveValue('练习文章')
  await expect(page.getByPlaceholder('文章正文...')).toHaveValue('短文章')
})

test('passage save failure is visible and keeps the filled form', async ({ page }) => {
  await page.route('**/api/v1/recitation/passages', route => route.fulfill(
    route.request().method() === 'POST' ? { status: 503, json: { detail: '服务暂时不可用' } } : { json: { passages: [] } },
  ))
  await page.goto('/recitation')
  await page.getByRole('button', { name: /新建段落/ }).click()
  await page.getByLabel('标题', { exact: true }).fill('静夜思')
  await page.getByLabel('原文内容', { exact: true }).fill('床前明月光')
  await page.getByRole('button', { name: /^创\s*建$/ }).click()
  await expect(page.getByText('创建失败：服务暂时不可用', { exact: true })).toBeVisible()
  await expect(page.getByLabel('原文内容', { exact: true })).toHaveValue('床前明月光')
})
