import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.INTERACTIVE_NATIVE_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Requires isolated native backend; no real AI')

for (const mode of ['restart', 'reload']) {
  test(`real interactive page completes and starts fresh after ${mode}`, async ({ page, request }) => {
    const start = new Date().toISOString(); const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    await request.post(ready.BackendURL + '/__test/reset')
    await stubBackend(page)
    await page.addInitScript(({ token }) => {
      localStorage.setItem('knowall_token', token)
      localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'round09c1-A', email: 'A@round09c1.invalid' }))
    }, { token: ready.Tokens.A })
    await page.route('**/api/v1/quiz/interactive/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname + new URL(r.request().url()).search }))
    const beginning = page.waitForResponse(r => r.url().includes('/quiz/interactive/start'))
    await page.goto('/quiz/interactive/sum')
    const firstStart = await beginning; expect(firstStart.status()).toBe(200)
    const first = await firstStart.json(); expect(first.questions).toHaveLength(2)
    expect(first.questions.every((q: any) => !('correct_answer' in q) && !('analysis' in q))).toBeTruthy()
    const answer = async (index: number, label: string) => {
      await expect(page.getByText(`原生交互第${index}题`, { exact: true })).toBeVisible()
      const pending = page.waitForResponse(r => r.url().endsWith('/quiz/interactive/submit') && r.request().method() === 'POST')
      await page.getByRole('radio', { name: label === 'A' ? 'A. 甲' : 'B. 乙', exact: true }).check()
      const response = await pending; expect(response.status()).toBe(200)
      return { Payload: response.request().postDataJSON(), Result: await response.json() }
    }
    const wrong = await answer(1, 'B'); expect(wrong.Result.is_correct).toBeFalsy()
    await expect(page.getByText('正确答案: A', { exact: true })).toBeVisible()
    await answer(2, 'A')
    await expect(page.getByText('答题完成！', { exact: true })).toBeVisible()
    await expect(page.getByText('正确率: 50.0% (1/2)', { exact: true })).toBeVisible()
    const nextStart = page.waitForResponse(r => r.url().includes('/quiz/interactive/start'))
    if (mode === 'reload') await page.reload()
    else await page.getByRole('button', { name: '重新开始' }).click()
    const secondStart = await nextStart; expect(secondStart.status()).toBe(200)
    const second = await secondStart.json(); expect(second.session_id).not.toBe(first.session_id)
    await answer(1, 'A'); await answer(2, 'A')
    await expect(page.getByText('正确率: 100.0% (2/2)', { exact: true })).toBeVisible()
    const headers = { Authorization: 'Bearer ' + ready.Tokens.A }
    const stats = await request.get(ready.BackendURL + `/api/v1/quiz/interactive/session/${first.session_id}/stats`, { headers })
    expect(stats.status()).toBe(200); expect((await stats.json()).accuracy).toBe(.5)
    const denied = await request.get(ready.BackendURL + `/api/v1/quiz/interactive/session/${first.session_id}/stats`, { headers: { Authorization: 'Bearer ' + ready.Tokens.B } })
    expect(denied.status()).toBe(404)
    const replay = await request.post(ready.BackendURL + '/api/v1/quiz/interactive/submit', { headers, data: wrong.Payload })
    expect(replay.status()).toBe(409)
    const final = await (await request.get(ready.BackendURL + '/__test/state')).json()
    expect(final.AnswerRecords).toHaveLength(4); expect(final.AIRequests).toHaveLength(0)
    expect(Object.keys(final.Sessions)).toHaveLength(2); expect(errors).toEqual([])
    writeFileSync(path.join(path.dirname(readyFile!), `real-browser-${mode}.json`), JSON.stringify({ Start: start, End: new Date().toISOString(), Simulation: true, JWTValidated: true, NoAIRequests: true, Mode: mode, First: first, Second: second, FirstStats: await stats.json(), ReplayStatus: replay.status(), Final: final, PageErrors: errors }, null, 2))
  })
}
