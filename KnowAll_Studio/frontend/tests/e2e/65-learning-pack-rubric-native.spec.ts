import { test, expect } from '@playwright/test'
import { stubBackend } from './fixtures'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const file = process.env.LEARNING_RUBRIC_NATIVE_READY
const ready = file ? JSON.parse(readFileSync(file, 'utf8')) : null
test.skip(!ready, 'Private real learning/JWT/SQLite and explicitly simulated AI required')

for (const mode of ['fill_blank', 'short_answer', 'repair']) {
  test(`native learning rubric ${mode} persistence practice and reread`, async ({ page, request }) => {
    const Start = new Date().toISOString(), errors: string[] = [], headers = { Authorization: 'Bearer '+ready.Tokens.A }
    page.on('pageerror', e => errors.push(e.message))
    expect((await request.post(ready.BackendURL+'/__test/reset', { data: { Mode: mode } })).status()).toBe(200)
    await stubBackend(page)
    await page.route('**/api/v1/documents**', r => r.fulfill({ json: [{ id: 'doc-1', filename: '隔离学习资料.md' }] }))
    await page.addInitScript(({ token }) => {
      localStorage.setItem('knowall_token', token)
      localStorage.setItem('knowall_user', JSON.stringify({ id: 'A', username: 'learning-A', email: 'A@09b3.invalid' }))
    }, { token: ready.Tokens.A })
    await page.route('**/api/v1/learning/**', r => r.continue({ url: ready.BackendURL+new URL(r.request().url()).pathname+new URL(r.request().url()).search }))
    let pack: any
    if (mode !== 'short_answer') {
      // Current UI has no fill-blank selector; create through the real supported API, then load it natively.
      const response = await request.post(ready.BackendURL+'/api/v1/learning/generate', { headers, data: {
        document_id: 'doc-1', question_count: 1, card_count: 0, model: 'explicit-simulation', question_types: ['fill_blank'], concurrency: 1,
      } })
      expect(response.status()).toBe(200); pack = await response.json()
    }
    await page.goto('/learning')
    await page.getByRole('combobox', { name: '学习文档' }).click()
    await page.getByText('隔离学习资料.md', { exact: true }).last().click()
    if (mode === 'short_answer') {
      await page.getByRole('spinbutton', { name: '习题数量' }).fill('1')
      await page.getByRole('spinbutton', { name: '卡片数量' }).fill('0')
      await page.getByRole('combobox', { name: '题型' }).click()
      await page.getByText('简答', { exact: true }).click()
      await page.keyboard.press('Escape')
      const generated = page.waitForResponse(r => r.url().endsWith('/learning/generate') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '生成学习包', exact: true }).click()
      const response = await generated; expect(response.status()).toBe(200); pack = await response.json()
    }
    const field = mode === 'short_answer' ? 'scoring_points' : 'blanks'
    expect(pack.sections[0].questions[0][field]).toHaveLength(2)
    const begin = async () => {
      const pending = page.waitForResponse(r => r.url().endsWith('/session') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '章节练习', exact: true }).click()
      const response = await pending; expect(response.status()).toBe(200)
      const session = await response.json()
      expect(session.items[0].question.blanks).toBeUndefined()
      expect(session.items[0].question.scoring_points).toBeUndefined()
    }
    const submit = async (answer: string, percent: number) => {
      await page.getByRole('textbox', { name: '你的答案', exact: true }).fill(answer)
      const pending = page.waitForResponse(r => r.url().endsWith('/answers') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '提交答案', exact: true }).click()
      const response = await pending; expect(response.status()).toBe(200)
      expect((await response.json()).score_ratio).toBe(percent/100)
      await expect(page.getByText(`得分：${percent}%`, { exact: true })).toBeVisible()
    }
    await begin(); await submit(mode === 'short_answer' ? 'reliable' : '氢;错', mode === 'short_answer' ? 75 : 50)
    await expect(page.getByText('继续巩固', { exact: true })).toBeVisible()
    await page.screenshot({ path: path.join(path.dirname(file!), `native-${mode}-partial.png`), fullPage: true })
    await page.reload()
    // selectedDoc is intentionally an in-memory store; select it again to reread the saved pack.
    await page.getByRole('combobox', { name: '学习文档' }).click()
    await page.getByText('隔离学习资料.md', { exact: true }).last().click()
    if (mode === 'repair') {
      await page.getByRole('tab', { name: '质检与缺漏' }).click()
      await page.getByRole('button', { name: '修复题目', exact: true }).click()
      await page.getByRole('textbox', { name: '正确答案', exact: true }).fill('新答案')
      const patched = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().includes('/questions/'))
      await page.getByRole('button', { name: '保存并质检', exact: true }).click()
      const response = await patched; expect(response.status()).toBe(200)
      expect((await response.json()).blanks).toEqual([])
      await expect(page.getByRole('dialog')).toHaveCount(0)
      await page.getByRole('tab', { name: '学习与练习' }).click()
    }
    await begin(); await submit(mode === 'short_answer' ? 'reliable flow' : mode === 'repair' ? '新答案' : '氢;氧', 100)
    await expect(page.getByText('回答正确', { exact: true })).toBeVisible()
    const final = await (await request.get(ready.BackendURL+'/__test/state')).json()
    const bank = final.Bank.find((q: any) => q.ID === pack.sections[0].questions[0].id)
    expect(bank[mode === 'short_answer' ? 'ScoringPoints' : 'Blanks']).toHaveLength(mode === 'repair' ? 0 : 2)
    const answers = final.Answers.filter((a: any) => a.QuestionID === bank.ID)
    expect(answers.map((a: any) => a.Correct)).toEqual([false, true])
    expect(final.AIRequests).toEqual([]); expect(final.ProviderRequests).toHaveLength(1)
    expect(errors).toEqual([])
    await page.screenshot({ path: path.join(path.dirname(file!), `native-${mode}-full.png`), fullPage: true })
    writeFileSync(path.join(path.dirname(file!), `real-browser-${mode}.json`), JSON.stringify({ Start, End: new Date().toISOString(),
      AIAdapterExplicitlyMocked: true, DocumentListingMocked: true, JWTValidated: true, Final: final, PageErrors: errors }, null, 2))
  })
}
