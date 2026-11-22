import { test, expect } from './fixtures'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.READING_NATIVE_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Opt in to private native reading backend with READING_NATIVE_READY; no real AI')

for (const action of ['switch', 'unmount'] as const) {
  test(`real browser HTTP stream ${action} cancels local simulated provider and recovers`, async ({ page, request }, info) => {
    const started = new Date().toISOString()
    const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    await request.post(ready.BackendURL + '/__test/reset')
    // Only reading APIs leave the hermetic fixture. Real router, SQL and native
    // provider HTTP run on localhost; fake auth seed is NOT JWT validation.
    await page.route('**/api/v1/reading/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname }))
    const state = async () => (await request.get(ready.BackendURL + '/__test/state')).json()
    await page.goto('/reading')
    await page.locator('.reading-article-item').filter({ hasText: '原生旧文章' }).click()
    await expect.poll(async () => (await state()).BackendSamples.some((s: any) => s.Path.endsWith('/convert/stream') && s.Chunks.some((c: string) => c.includes('token')))).toBe(true)
    const during = await state()
    expect(during.CacheRows).toEqual([])
    if (action === 'switch') {
      await page.locator('.reading-article-item').filter({ hasText: '原生新文章' }).click()
    } else {
      await page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
      await expect(page.locator('.reading-main')).toHaveCount(0)
    }
    await expect.poll(async () => (await state()).Closed.includes('old')).toBe(true)
    if (action === 'unmount') {
      expect(await page.evaluate(() => localStorage.getItem('reading_lang_vocab'))).toBeNull()
      expect((await state()).CacheRows).toEqual([])
      await page.goto('/reading')
      await page.locator('.reading-article-item').filter({ hasText: '原生新文章' }).click()
    }
    await expect(page.locator('.reading-text')).toContainText('新结果 world')
    await expect(page.locator('.reading-main-header h2')).toContainText('原生新文章')
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
    expect(saved.totalRead).toBe(1)
    const final = await state()
    expect(final.ProviderRequests).toHaveLength(2)
    expect(final.CacheRows).toHaveLength(1)
    expect(final.CacheRows[0].Result).toBe('新结果 world')
    expect(final.BackendSamples.filter((s: any) => s.Path.endsWith('/convert'))).toHaveLength(0)
    expect(errors).toEqual([])
    const output = path.resolve(path.dirname(readyFile!), process.env.ROUND_STAGE || 'native')
    mkdirSync(output, { recursive: true })
    writeFileSync(path.join(output, `real-browser-${action}.json`), JSON.stringify({ Start: started, End: new Date().toISOString(), Simulation: true, JWTValidated: false, Action: action, During: during, Final: final, Saved: saved, PageErrors: errors }, null, 2))
  })
}
