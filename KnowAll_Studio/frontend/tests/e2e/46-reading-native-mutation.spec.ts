import { test, expect } from './fixtures'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.READING_MUTATION_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Opt in to private native reading backend with READING_MUTATION_READY; no real AI/JWT')
const title = '原生保存资料'
const body = '我们通过认真学习理解这个世界，并且每天阅读新的知识内容。\r\n第二段保留正文。'
const modal = (page: any) => page.getByRole('dialog').filter({ hasText: '上传文章' })
const saved = (page: any) => page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
const pick = (page: any, title: string) => page.locator('.reading-article-item').filter({ hasText: title })

for (const action of ['txt-save', 'save-switch', 'save-unmount', 'delete-stream', 'delete-switch'] as const) {
  test(`native reading mutation ${action} preserves SQL and latest browser intent`, async ({ page, request }) => {
    const start = new Date().toISOString(); const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    const mode = action.startsWith('save-') ? 'hold-upload' : action === 'delete-switch' ? 'hold-delete' : 'normal'
    await request.post(ready.BackendURL + '/__test/reset', { data: { Mode: mode } })
    await page.route('**/api/v1/reading/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname }))
    const state = async () => (await request.get(ready.BackendURL + '/__test/state')).json()
    await page.goto('/reading')
    let during: any
    if (action === 'txt-save' || action.startsWith('save-')) {
      await page.getByRole('button', { name: '+ 上传文章', exact: true }).click()
      if (action === 'txt-save') {
        await modal(page).locator('input[type=file]').setInputFiles({ name: 'native-reading.txt', mimeType: 'text/plain', buffer: Buffer.from(title + '\r\n' + body) })
        await expect(modal(page).getByPlaceholder('文章标题（必填）')).toHaveValue(title)
        await expect(modal(page).getByPlaceholder('文章正文...')).toHaveValue(body.replace(/\r\n/g, '\n'))
      } else {
        await modal(page).getByPlaceholder('文章标题（必填）').fill(title)
        await modal(page).getByPlaceholder('文章正文...').fill(body)
      }
      // response fires on headers, before the fixture releases its held body.
      const received = action.startsWith('save-')
        ? page.waitForResponse(r => r.url().endsWith('/reading/articles/upload')) : null
      await modal(page).getByRole('button', { name: '保存', exact: true }).click()
      await expect.poll(async () => (await state()).Articles.some((a: any) => a.Title === title)).toBe(true)
      during = await state()
      if (action.startsWith('save-')) {
        await expect.poll(async () => (await state()).MutationHeld.includes('/api/v1/reading/articles/upload')).toBe(true)
        expect(during.ProviderRequests).toHaveLength(0)
        if (action === 'save-switch') {
          await modal(page).getByRole('button', { name: '取消', exact: true }).click()
          await pick(page, '原生新文章').click()
          await expect(page.locator('.reading-text')).toContainText('新结果 world')
        } else {
          await page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
          await expect(page.locator('.reading-main')).toHaveCount(0)
        }
        await request.post(ready.BackendURL + '/__test/release-write')
        await (await received!).finished()
        await expect.poll(async () => (await state()).MutationReleased.length).toBe(1)
        if (action === 'save-switch') {
          await expect(page.getByText('文章已保存！', { exact: true })).toBeVisible()
          await expect.poll(async () => (await state()).BackendSamples.filter((s: any) => s.Path === '/api/v1/reading/articles').length).toBe(2)
          await expect(page.locator('.reading-main-header h2')).toContainText('原生新文章')
          expect((await saved(page)).totalRead).toBe(1)
        } else {
          // Completed XHR plus React task boundaries, no server result replay.
          await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
          expect(await saved(page)).toEqual({})
        }
      } else {
        await expect(page.locator('.reading-main-header h2')).toContainText(title)
        await expect(page.locator('.reading-text')).toContainText('新结果 world')
        expect((await saved(page)).totalRead).toBe(1)
      }
      const current = await state()
      const row = current.Articles.find((a: any) => a.Title === title)
      expect(row.Content).toBe(action === 'txt-save' ? body : body.replace(/\r\n/g, '\n'))
      expect(row.ChineseCount).toBeGreaterThanOrEqual(20)
      expect(current.BackendSamples.filter((s: any) => s.Path.endsWith('/articles/upload'))).toHaveLength(1)
      expect(current.ProviderRequests).toHaveLength(action === 'save-unmount' ? 0 : 1)
      if (action === 'save-unmount') {
        expect(current.BackendSamples.filter((s: any) => s.Path === '/api/v1/reading/articles')).toHaveLength(1)
        expect(current.CacheRows).toHaveLength(0)
      } else expect(current.CacheRows).toHaveLength(1)
    } else {
      await pick(page, '原生旧文章').click()
      await expect.poll(async () => (await state()).BackendSamples.some((s: any) => s.Path.endsWith('/convert/stream') && s.Chunks.some((c: string) => c.includes('token')))).toBe(true)
      during = await state(); expect(during.CacheRows).toEqual([])
      await pick(page, '原生旧文章').getByTitle('删除', { exact: true }).click()
      const received = action === 'delete-switch'
        ? page.waitForResponse(r => r.request().method() === 'DELETE' && r.url().endsWith('/articles/u1')) : null
      await page.getByRole('button', { name: /删\s*除/ }).click()
      await expect.poll(async () => (await state()).Articles.some((a: any) => a.ID === 1)).toBe(false)
      if (action === 'delete-switch') {
        await expect.poll(async () => (await state()).MutationHeld.length).toBe(1)
        await page.getByRole('dialog').filter({ hasText: '确定删除这篇文章' }).getByRole('button', { name: /取\s*消/ }).click()
        await pick(page, '原生新文章').click(); await expect(page.locator('.reading-text')).toContainText('新结果 world')
        await request.post(ready.BackendURL + '/__test/release-write'); await (await received!).finished()
        await expect(page.getByText('已删除', { exact: true })).toBeVisible()
        await expect.poll(async () => (await state()).MutationReleased.length).toBe(1)
        await expect(page.locator('.reading-main-header h2')).toContainText('原生新文章')
        await expect(page.locator('.reading-text')).toContainText('新结果 world')
      } else {
        await expect(page.getByText('已删除', { exact: true })).toBeVisible()
        await expect.poll(async () => (await state()).Closed.includes('old')).toBe(true)
        expect((await state()).CacheRows).toEqual([])
        expect(await saved(page)).toEqual({})
        await expect(page.getByRole('button', { name: /换一批词/ })).toBeEnabled()
        await expect(page.locator('.reading-text')).toHaveCount(0)
        await pick(page, '原生新文章').click(); await expect(page.locator('.reading-text')).toContainText('新结果 world')
      }
      const current = await state(); expect(current.Closed).toContain('old')
      expect(current.ProviderRequests).toHaveLength(2)
      expect(current.CacheRows).toHaveLength(1)
      expect((await saved(page)).totalRead).toBe(1)
    }
    const final = await state()
    expect(final.BackendSamples.filter((s: any) => s.Path.endsWith('/convert'))).toHaveLength(0)
    expect(errors).toEqual([])
    const output = path.resolve(path.dirname(readyFile!), process.env.ROUND_STAGE || 'native')
    mkdirSync(output, { recursive: true })
    writeFileSync(path.join(output, `real-mutation-${action}.json`), JSON.stringify({ Start: start, End: new Date().toISOString(), Simulation: true, JWTValidated: false, Action: action, During: during, Final: final, Saved: await saved(page), PageErrors: errors }, null, 2))
  })
}

