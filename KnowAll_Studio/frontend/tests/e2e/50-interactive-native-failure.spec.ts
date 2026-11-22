import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.INTERACTIVE_NATIVE_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Requires private SQLite/JWT server; failure controls are explicit simulation, no AI')
for (const mode of ['sql-retry', 'committed-leave']) {
  test(`native interactive ${mode} preserves truthful SQL and current page`, async ({ page, request }) => {
    const start = new Date().toISOString(); const errors: string[] = []; let aborted = false
    page.on('pageerror', e => errors.push(e.message))
    page.on('requestfailed', r => { if (r.url().endsWith('/quiz/interactive/submit')) aborted = true })
    await request.post(ready.BackendURL + '/__test/reset'); await stubBackend(page)
    await page.addInitScript(({ token }) => { localStorage.setItem('knowall_token', token); localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'round09c2a-A', email: 'A@fixture.invalid' })) }, { token: ready.Tokens.A })
    await page.route('**/api/v1/quiz/interactive/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname + new URL(r.request().url()).search }))
    const state = async () => (await request.get(ready.BackendURL + '/__test/state')).json()
    await page.goto('/quiz/interactive/sum')
    await expect(page.getByText('原生交互第1题', { exact: true })).toBeVisible()
    if (mode === 'sql-retry') {
      await request.post(ready.BackendURL + '/__test/fail-once')
      const failed = page.waitForResponse(r => r.url().endsWith('/quiz/interactive/submit'))
      await page.getByRole('radio', { name: 'A. 甲' }).check(); expect((await failed).status()).toBe(500)
      await expect(page.getByText(/提交失败:/)).toBeVisible()
      const before = await state(); expect(before.AnswerRecords).toHaveLength(0); expect(before.CommitFailures).toBe(1)
      await expect(page.getByRole('radio', { name: 'A. 甲' })).toBeChecked()
      const retried = page.waitForResponse(r => r.url().endsWith('/quiz/interactive/submit'))
      await page.getByRole('button', { name: '重新提交', exact: true }).click(); expect((await retried).status()).toBe(200)
    } else {
      await request.post(ready.BackendURL + '/__test/hold-response')
      await page.getByRole('radio', { name: 'A. 甲' }).check()
      await expect.poll(async () => (await state()).AnswerRecords.length).toBe(1)
      const committed = await state(); expect(committed.CommittedResponseHeld).toBeTruthy()
      await page.getByRole('link', { name: '全部题库', exact: true }).click(); await expect(page).toHaveURL(/\/quiz$/)
      await expect.poll(() => aborted).toBe(true)
      await page.evaluate(() => { history.pushState(null, '', '/quiz/interactive/sum'); dispatchEvent(new PopStateEvent('popstate')) })
      await expect(page.getByRole('button', { name: '答题卡 (0/2)' })).toBeVisible()
      await request.post(ready.BackendURL + '/__test/release')
      await expect(page.getByRole('radio', { name: 'A. 甲' })).toBeEnabled(); await page.getByRole('radio', { name: 'A. 甲' }).check()
    }
    await expect(page.getByText('原生交互第2题', { exact: true })).toBeVisible(); await page.getByRole('radio', { name: 'A. 甲' }).check()
    await expect(page.getByText('正确率: 100.0% (2/2)', { exact: true })).toBeVisible()
    const final = await state(); expect(final.AnswerRecords).toHaveLength(mode === 'sql-retry' ? 2 : 3)
    expect(final.AIRequests).toHaveLength(0); expect(errors).toEqual([])
    writeFileSync(path.join(path.dirname(readyFile!), `real-browser-${mode}.json`), JSON.stringify({ Start: start, End: new Date().toISOString(), Simulation: true, JWTValidated: true, NoAIRequests: true, Mode: mode, Aborted: aborted, Final: final, PageErrors: errors }, null, 2))
  })
}
