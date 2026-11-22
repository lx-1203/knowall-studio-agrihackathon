import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.QUIZ_RUBRIC_NATIVE_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Private real quiz/JWT/SQLite with explicitly simulated local generation required')

for (const mode of ['fill_blank', 'short_answer', 'restart']) {
  test(`native generated rubric ${mode} survives bank and exam assembly`, async ({ page, request }) => {
    const Start = new Date().toISOString(), errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    await request.post(ready.BackendURL + '/__test/reset', { data: { Mode: mode } })
    await stubBackend(page)
    await page.route('**/api/v1/documents**', r => r.fulfill({ json: [{ id: 'doc-1', filename: '测试文档.pdf' }] }))
    await page.addInitScript(({ token }) => {
      localStorage.setItem('knowall_token', token)
      localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'rubric-A', email: 'A@09b1.invalid' }))
    }, { token: ready.Tokens.A })
    await page.route('**/api/v1/quiz/**', r => r.continue({ url: ready.BackendURL + new URL(r.request().url()).pathname + new URL(r.request().url()).search }))
    await page.goto('/quiz')
    await page.getByRole('combobox').first().click()
    await page.getByText('测试文档.pdf', { exact: true }).last().click()
    const generated = page.waitForResponse(r => r.url().endsWith('/quiz/generate') && r.request().method() === 'POST')
    await page.getByRole('button', { name: /生成预览/ }).click()
    const previewResponse = await generated; expect(previewResponse.status()).toBe(200)
    const Preview = await previewResponse.json()
    expect(Preview.questions[0][mode === 'short_answer' ? 'scoring_points' : 'blanks']).toHaveLength(2)
    let Restart: any = null
    if (mode === 'restart') {
      const saved = page.waitForResponse(r => r.url().endsWith('/quiz/bank/save') && r.request().method() === 'POST')
      await page.getByRole('button', { name: /入库 \(1\)/ }).click()
      expect((await saved).status()).toBe(200)
      await expect(page.getByRole('button', { name: /入库 \(1\)/ })).toHaveCount(0)
      Restart = await (await request.post(ready.BackendURL + '/__test/restart')).json()
      expect(Restart.BeforePID).not.toBe(Restart.AfterPID); expect(Restart.PreviousExit).toBe(0)
      await page.reload()
      await page.getByRole('tab', { name: /题库/ }).click()
      await page.getByRole('button', { name: /一键刷题 \(1题\)/ }).click()
    } else {
      await page.getByRole('button', { name: /组卷答题/ }).click()
    }
    await expect(page.getByRole('button', { name: /提交 \(0\/1\)/ })).toBeVisible()
    if (mode === 'short_answer') {
      const card = page.getByText('原生评分点问题', { exact: true }).locator('xpath=ancestor::div[contains(@class, "ant-card-small")][1]')
      await card.getByRole('textbox').fill('reliable transfer')
    } else {
      await page.getByPlaceholder('第一元素', { exact: true }).fill('氢')
      await page.getByPlaceholder('第二元素', { exact: true }).fill('错误答案一')
    }
    const submitted = page.waitForResponse(r => r.url().endsWith('/quiz/exam/submit') && r.request().method() === 'POST')
    await page.getByRole('button', { name: /提交 \(1\/1\)/ }).click()
    const response = await submitted; expect(response.status()).toBe(200)
    const Result = await response.json()
    expect(Result.details[0].partial_score).toBe(mode === 'short_answer' ? .75 : .5)
    expect(Result.score).toBe(mode === 'short_answer' ? 3.75 : 2.5)
    expect(Result.percentage).toBe(mode === 'short_answer' ? 75 : 50)
    expect(Result.details[0].grading_method).toBe('local'); expect(Result.semantic_graded_count).toBe(0)
    if (mode === 'short_answer') await expect(page.getByText(/要点得分: 3\/4/)).toBeVisible()
    else await expect(page.getByText('部分得分: 50%', { exact: true })).toBeVisible()
    const Final = await (await request.get(ready.BackendURL + '/__test/state')).json()
    expect(Final.Bank).toHaveLength(1); expect(Final.Answers).toHaveLength(1); expect(Final.Answers[0].User).toBe('A')
    const workers = [...Final.PreviousWorkers, Final]
    expect(workers.flatMap((w: any) => w.AIRequests)).toEqual([])
    expect(workers.flatMap((w: any) => w.ProviderRequests)).toHaveLength(1)
    expect(errors).toEqual([])
    await page.screenshot({ path: path.join(path.dirname(readyFile!), `native-${mode}.png`), fullPage: true })
    writeFileSync(path.join(path.dirname(readyFile!), `real-browser-${mode}.json`), JSON.stringify({ Start, End: new Date().toISOString(), AIAdapterExplicitlyMocked: true, JWTValidated: true, Preview, Restart, Result, Final, PageErrors: errors }, null, 2))
  })
}
