import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.QUIZ_NATIVE_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Requires isolated native quiz backend; external AI explicitly simulated')

for (const mode of ['invalid', 'valid']) {
  test(`actual quiz page grades six types with ${mode} simulated semantic result`, async ({ page, request }) => {
    const start = new Date().toISOString(); const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    await request.post(ready.BackendURL + '/__test/reset', { data: { Mode: mode } })
    await stubBackend(page)
    await page.addInitScript(({ token }) => {
      localStorage.setItem('knowall_token', token)
      localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'round09a-A', email: 'A@round09a.invalid' }))
    }, { token: ready.Tokens.A })
    await page.route('**/api/v1/quiz/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname + new URL(r.request().url()).search }))
    await page.goto('/quiz')
    await page.getByRole('tab', { name: /题库/ }).click()
    await page.getByRole('button', { name: /一键刷题 \(6题\)/ }).click()
    await expect(page.getByRole('button', { name: /提交 \(0\/6\)/ })).toBeVisible()
    const card = (stem: string) => page.getByText(stem, { exact: true }).locator('xpath=ancestor::div[contains(@class, "ant-card-small")][1]')
    await card('原生单选').getByRole('radio', { name: 'A. 选项A', exact: true }).check()
    await card('原生多选').getByRole('checkbox', { name: 'C. 选项C', exact: true }).check()
    await card('原生多选').getByRole('checkbox', { name: 'A. 选项A', exact: true }).check()
    await card('原生判断').getByRole('radio', { name: 'A. 正确', exact: true }).check()
    await card('原生填空').getByRole('textbox').fill('氢')
    await card('原生计算').getByRole('textbox').fill('2.005')
    await card('原生简答').getByRole('textbox').fill('unrelated wrong statement')
    const response = page.waitForResponse(r => r.url().endsWith('/quiz/exam/submit') && r.request().method() === 'POST')
    await page.getByRole('button', { name: /提交 \(6\/6\)/ }).click()
    const first = await response; expect(first.status()).toBe(200); const result = await first.json()
    const expected = mode === 'invalid' ? 25 : 30
    expect(result.score).toBe(expected); expect(result.total).toBe(6)
    expect(result.semantic_graded_count).toBe(mode === 'invalid' ? 0 : 1)
    await expect(page.getByText(`${expected}/30 (${mode === 'invalid' ? '83.3' : '100'}%)`, { exact: true })).toBeVisible()
    if (mode === 'valid') await expect(page.getByText('建议: 明确模拟建议', { exact: true })).toBeVisible()
    const state = async () => (await request.get(ready.BackendURL + '/__test/state')).json()
    const before = await state(); expect(before.AnswerRecords).toHaveLength(6); expect(before.ProviderRequests).toHaveLength(1)
    // The results screen hides Submit. Probe a replay from the same real browser/JWT.
    const duplicate = await page.evaluate(async ({ url, data, token }) => {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(data) })
      return { Status: r.status, Body: await r.json() }
    }, { url: ready.BackendURL + '/api/v1/quiz/exam/submit', data: first.request().postDataJSON(), token: ready.Tokens.A })
    expect(duplicate.Status).toBe(400); expect(duplicate.Body.detail).toContain('不能重复提交')
    const final = await state(); expect(final.AnswerRecords).toEqual(before.AnswerRecords)
    expect(final.Errors).toHaveLength(mode === 'invalid' ? 1 : 0); expect(final.ProviderRequests).toHaveLength(1)
    expect(errors).toEqual([])
    writeFileSync(path.join(path.dirname(readyFile!), `real-browser-${mode}.json`), JSON.stringify({ Start: start, End: new Date().toISOString(), Simulation: true, JWTValidated: true, AIAdapterMocked: true, Mode: mode, Result: result, Duplicate: duplicate, Final: final, PageErrors: errors }, null, 2))
  })
}

test('reload rejects old paper replay and permits a fresh paper', async ({ page, request }) => {
  const start = new Date().toISOString(); const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await request.post(ready.BackendURL + '/__test/reset', { data: { Mode: 'invalid' } })
  await stubBackend(page)
  await page.addInitScript(({ token }) => {
    localStorage.setItem('knowall_token', token)
    localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'round09a-A', email: 'A@round09a.invalid' }))
  }, { token: ready.Tokens.A })
  await page.route('**/api/v1/quiz/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname + new URL(r.request().url()).search }))
  await page.goto('/quiz')
  const submitFresh = async () => {
    await page.getByRole('tab', { name: /题库/ }).click()
    await page.getByRole('button', { name: /一键刷题 \(6题\)/ }).click()
    const card = (stem: string) => page.getByText(stem, { exact: true }).locator('xpath=ancestor::div[contains(@class, "ant-card-small")][1]')
    await card('原生单选').getByRole('radio', { name: 'A. 选项A', exact: true }).check()
    await card('原生多选').getByRole('checkbox', { name: 'A. 选项A', exact: true }).check()
    await card('原生多选').getByRole('checkbox', { name: 'C. 选项C', exact: true }).check()
    await card('原生判断').getByRole('radio', { name: 'A. 正确', exact: true }).check()
    await card('原生填空').getByRole('textbox').fill('氢')
    await card('原生计算').getByRole('textbox').fill('2')
    const pending = page.waitForResponse(r => r.url().endsWith('/quiz/exam/submit') && r.request().method() === 'POST')
    await page.getByRole('button', { name: /提交 \(5\/6\)/ }).click()
    const response = await pending; expect(response.status()).toBe(200)
    const result = await response.json(); expect(result.score).toBe(25); expect(result.semantic_graded_count).toBe(0)
    await expect(page.getByText('25/30 (83.3%)', { exact: true })).toBeVisible()
    return { Payload: response.request().postDataJSON(), Result: result }
  }
  const first = await submitFresh()
  const state = async () => (await request.get(ready.BackendURL + '/__test/state')).json()
  const before = await state(); expect(before.AnswerRecords).toHaveLength(6)
  await page.reload()
  await page.getByRole('tab', { name: /答题/ }).click()
  await expect(page.getByText('暂无进行中的试卷', { exact: true })).toBeVisible()
  // Existing quiz store is memory-only: reload resets it; replay still uses the old real paper.
  const duplicate = await page.evaluate(async ({ url, data, token }) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(data) })
    return { Status: r.status, Body: await r.json() }
  }, { url: ready.BackendURL + '/api/v1/quiz/exam/submit', data: first.Payload, token: ready.Tokens.A })
  expect(duplicate.Status).toBe(400)
  expect((await state()).AnswerRecords).toEqual(before.AnswerRecords)
  const second = await submitFresh()
  expect(second.Payload.paper_id).not.toBe(first.Payload.paper_id)
  const final = await state(); expect(final.AnswerRecords).toHaveLength(12)
  expect(final.ProviderRequests).toHaveLength(0); expect(errors).toEqual([])
  writeFileSync(path.join(path.dirname(readyFile!), 'real-browser-recovery.json'), JSON.stringify({ Start: start, End: new Date().toISOString(), Simulation: true, JWTValidated: true, AIAdapterMocked: true, First: first, Duplicate: duplicate, Second: second, Final: final, PageErrors: errors }, null, 2))
})
