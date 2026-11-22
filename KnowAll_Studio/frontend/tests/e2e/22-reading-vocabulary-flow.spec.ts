import { test, expect } from './fixtures'

const article = { id: 1, title: '阅读流程', content: '我们的世界很大，让我们通过学习了解能量。', difficulty: 1, tags: [] }
const vocabulary = [{ cn: '能量', en: 'energy' }]

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/reading/articles*', route => route.fulfill({ json: [article] }))
  await page.route('**/api/v1/reading/articles/1', route => route.fulfill({ json: article }))
})

for (const cached of [false, true]) {
  test(`${cached ? 'cached' : 'streamed'} terminal result renders and refresh bypasses cache`, async ({ page }) => {
    const requests: any[] = []
    await page.route('**/api/v1/reading/convert/stream', route => {
      requests.push(route.request().postDataJSON())
      const event = { ...(cached ? { cached: true } : { done: true }), result: '今天学习 energy 的最终正文', vocabulary }
      return route.fulfill({ contentType: 'text/event-stream', body: (cached ? '' : 'data: {"token":"半截正文"}\n\n') + `data: ${JSON.stringify(event)}\n\n` })
    })
    await page.goto('/reading')
    await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
    await expect(page.locator('.reading-text')).toContainText('最终正文')
    await expect(page.locator('.reading-panel-badge')).toHaveText('1')
    await page.getByRole('button', { name: /换一批词/ }).click()
    await expect.poll(() => requests.length).toBe(2)
    expect(requests[0].skip_cache).toBe(false)
    expect(requests[1].skip_cache).toBe(true)
    expect(requests[1].level).toBe(1)
    expect(requests[1].seen_words).toContainEqual(vocabulary[0])
    await expect(page.locator('.reading-text')).toContainText('最终正文')
  })
}

test('truncated stream restores final text with non-streaming fallback', async ({ page }) => {
  let fallbacks = 0
  await page.route('**/api/v1/reading/convert/stream', route => route.fulfill({ contentType: 'text/event-stream', body: 'data: {"token":"半截正文"}\n\n' }))
  await page.route('**/api/v1/reading/convert', route => { fallbacks++; return route.fulfill({ json: { result: '恢复后的 energy 正文', vocabulary } }) })
  await page.goto('/reading')
  await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
  await expect(page.locator('.reading-text')).toContainText('恢复后的')
  expect(fallbacks).toBe(1)
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
  expect(saved.totalRead).toBe(1)
})

test('generate from optional summary, then master and reload returned word', async ({ page }) => {
  let generated = false
  let mastered = false
  await page.route('**/api/v1/documents/**', route => route.fulfill({ json: { id: 'biology', filename: 'biology.txt' } }))
  await page.route('**/api/v1/language/vocabulary**', route => {
    const request = route.request()
    const word = { id: 'saved-energy', word: 'energy', definition: '能量', part_of_speech: 'noun', difficulty: 'easy', mastered }
    if (request.url().endsWith('/generate')) {
      expect(request.postDataJSON()).toEqual({ document_id: 'biology' })
      generated = true
      return route.fulfill({ json: { agent: 'language', status: 'success', result: { total_words: 1, words: [word] } } })
    }
    if (request.method() === 'PATCH') { mastered = true; return route.fulfill({ json: { id: word.id, mastered } }) }
    return route.fulfill({ json: { total: generated ? 1 : 0, words: generated ? [word] : [] } })
  })
  await page.goto('/language?docId=biology')
  await page.getByRole('button', { name: /生成生词表/ }).first().click()
  await page.getByRole('button', { name: /开始生成/ }).click()
  await expect(page.getByText('energy', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /标记已掌握/ }).click()
  await page.reload()
  await expect(page.getByRole('button', { name: /已掌握/ })).toBeVisible()
})

test('model vocabulary remains plain text and cannot inject HTML', async ({ page }) => {
  // Capture the committed DOM before passive effects can replace unsafe markup.
  await page.addInitScript(() => {
    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML')!
    Object.defineProperty(Element.prototype, 'innerHTML', {
      ...descriptor,
      set(value) {
        descriptor.set!.call(this, value)
        if (this.classList.contains('reading-text') && this.querySelector('img')) {
          (window as any).readingInjectedMarkup = true
        }
      },
    })
  })
  const unsafe = '<img src=x onerror="window.readingInjection=true">'
  await page.route('**/api/v1/reading/convert/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: `data: ${JSON.stringify({ done: true, result: 'energy', vocabulary: [{ cn: unsafe, en: 'energy' }] })}\n\n`,
  }))
  await page.goto('/reading')
  await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
  await expect(page.locator('.reading-text')).toBeVisible()
  await expect(page.locator('.reading-text img')).toHaveCount(0)
  await expect(page.locator('.reading-text')).toContainText(unsafe)
  expect(await page.evaluate(() => (window as any).readingInjection)).toBeUndefined()
  expect(await page.evaluate(() => (window as any).readingInjectedMarkup)).toBeUndefined()
})

test('manual level and refresh survive a non-streaming fallback', async ({ page }) => {
  const requests: any[] = []
  await page.route('**/api/v1/reading/convert/stream', route => route.fulfill({ status: 503, json: { detail: 'outage' } }))
  await page.route('**/api/v1/reading/convert', route => {
    requests.push(route.request().postDataJSON())
    return route.fulfill({ json: { result: '成功 energy', vocabulary } })
  })
  await page.goto('/reading')
  await page.getByRole('button', { name: /掌握者/ }).click()
  await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
  await expect(page.locator('.reading-text')).toContainText('成功')
  await page.getByRole('button', { name: /换一批词/ }).click()
  await expect.poll(() => requests.length).toBe(2)
  expect(requests[0].level).toBe(3)
  expect(requests[1].skip_cache).toBe(true)
})

test('failed stream and fallback leave input intact and do not count as reading', async ({ page }) => {
  await page.route('**/api/v1/reading/convert/stream', route => route.fulfill({ contentType: 'text/event-stream', body: 'data: {"error":"unavailable"}\n\n' }))
  await page.route('**/api/v1/reading/convert', route => route.fulfill({ status: 400, json: { detail: 'unavailable' } }))
  await page.goto('/reading')
  await page.getByRole('button', { name: /自定义/ }).click()
  const input = page.getByPlaceholder(/在此粘贴中文文本/)
  await input.fill(article.content)
  await page.getByRole('button', { name: /开始阅读/ }).click()
  await expect(page.getByText('请求失败，请检查服务是否运行')).toBeVisible()
  await expect(input).toHaveValue(article.content)
  expect(await page.evaluate(() => localStorage.getItem('reading_lang_vocab'))).toBeNull()
})

test('CRLF terminal without trailing newline completes exactly once', async ({ page }) => {
  await page.route('**/api/v1/reading/convert/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: ': keepalive\r\ndata: {"token":"partial"}\r\n\r\ndata: ' + JSON.stringify({ done: true, result: '最终 energy', vocabulary }),
  }))
  await page.goto('/reading')
  await page.locator('.reading-article-item').filter({ hasText: article.title }).click()
  await expect(page.locator('.reading-text')).toContainText('最终')
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
  expect(saved.totalRead).toBe(1)
  expect(saved.words.energy.seen).toBe(1)
})
