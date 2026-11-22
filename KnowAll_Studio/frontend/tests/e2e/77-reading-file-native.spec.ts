import { test, expect } from './fixtures'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
const readyFile = process.env.READING_FILE_NATIVE_READY
const ready = readyFile ? JSON.parse(readFileSync(readyFile, 'utf8')) : null
test.skip(!ready, 'Requires private reading/SQLite/local simulated provider; no real AI/JWT')
const body = '我们通过认真学习理解这个世界，并且每天阅读新的知识内容。\r\n第二段 emoji 🌞 café �。'
const modal = (page: any) => page.getByRole('dialog').filter({ hasText: '上传文章' })
const title = (page: any) => modal(page).getByPlaceholder('文章标题（必填）')
const content = (page: any) => modal(page).getByPlaceholder('文章正文...')
const input = (page: any) => modal(page).locator('input[type=file]')

for (const scenario of ['bom-crlf', 'invalid-recovery', 'gbk-recovery', 'blank-title-recovery', 'same-file-repeat'] as const) {
  test(`native browser bytes ${scenario} keeps persisted article exact`, async ({ page, request }) => {
    const started = new Date().toISOString(); const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    await request.post(ready.BackendURL+'/__test/reset', { data: { Mode: 'normal' } })
    await page.route('**/api/v1/reading/**', r => r.continue({ url: ready.BackendURL+new URL(r.request().url()).pathname }))
    const state = async () => (await request.get(ready.BackendURL+'/__test/state')).json()
    await page.goto('/reading')
    await page.getByRole('button', { name: '+ 上传文章', exact: true }).click()
    const wantedTitle = '原生字节-'+scenario
    const valid = Buffer.concat([Buffer.from([0xef,0xbb,0xbf]), Buffer.from(wantedTitle+'\r\n'+body)])
    const choose = async (raw: Buffer) => {
      await input(page).focus()
      await input(page).setInputFiles({ name: 'same-reading.TXT', mimeType: 'text/plain', buffer: raw })
    }
    let rejected: any = null
    if (scenario === 'invalid-recovery' || scenario === 'gbk-recovery' || scenario === 'blank-title-recovery') {
      const bad = scenario === 'gbk-recovery'
        ? Buffer.concat([Buffer.from('legacy\n'),Buffer.from(Array(25).fill([0xd6,0xd0]).flat())])
        : scenario === 'invalid-recovery'
        ? Buffer.concat([Buffer.from('坏字节标题\n'+body),Buffer.from([0xe4,0xb8])])
        : Buffer.from('\n错误移位标题\n'+body)
      await choose(bad)
      await expect(page.getByText(scenario !== 'blank-title-recovery'
        ? '文件编码无效，请另存为 UTF-8 后重新上传' : '文件格式错误：首行为标题，之后为正文', { exact: true })).toBeVisible()
      await expect(title(page)).toHaveValue('')
      await expect(content(page)).toHaveValue('')
      rejected = await state()
      expect(rejected.Articles).toHaveLength(2)
      expect(rejected.BackendSamples.filter((s: any) => s.Method === 'POST')).toEqual([])
      expect(rejected.ProviderRequests).toEqual([])
      expect(rejected.CacheRows).toEqual([])
    }
    await choose(valid)
    await expect(title(page)).toHaveValue(wantedTitle)
    await expect(content(page)).toHaveValue(body.replace(/\r\n/g,'\n'))
    await expect(input(page)).toHaveValue('')
    if (scenario === 'same-file-repeat') {
      await title(page).fill('手工临时标题')
      await content(page).fill('手工临时正文')
      await choose(valid)
      await expect(title(page)).toHaveValue(wantedTitle)
      await expect(content(page)).toHaveValue(body.replace(/\r\n/g,'\n'))
    }
    await page.screenshot({ path: test.info().outputPath('accepted-file.png') })
    await modal(page).getByRole('button', { name: '保存', exact: true }).click()
    await expect(page.locator('.reading-main-header h2')).toContainText(wantedTitle)
    await expect(page.locator('.reading-text')).toContainText('新结果 world')
    await expect.poll(async () => (await state()).CacheRows.length).toBe(1)
    const final = await state()
    const row = final.Articles.find((a: any) => a.Title === wantedTitle)
    expect(row.Content).toBe(body)
    expect(row.ChineseCount).toBeGreaterThanOrEqual(20)
    expect(final.Articles).toHaveLength(3)
    const upload = final.BackendSamples.filter((s: any) => s.Path.endsWith('/articles/upload'))
    expect(upload).toHaveLength(1)
    expect(JSON.parse(upload[0].RequestBody)).toEqual({ title: wantedTitle, content: body })
    expect(upload[0].Status).toBe(200)
    expect(final.ProviderRequests).toHaveLength(1)
    expect(final.ProviderRequests[0].Simulation).toBe(true)
    expect(final.BackendSamples.filter((s: any) => s.Path.endsWith('/convert'))).toHaveLength(0)
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('reading_lang_vocab') || '{}'))
    expect(saved.totalRead).toBe(1)
    const articlePath = '/api/v1/reading/articles/u'+row.ID
    const detail = await request.get(ready.BackendURL+articlePath)
    expect(detail.status()).toBe(200)
    expect((await detail.json()).content).toBe(body)
    const removed = await request.delete(ready.BackendURL+articlePath)
    expect(removed.status()).toBe(200)
    const missing = await request.get(ready.BackendURL+articlePath)
    expect(missing.status()).toBe(404)
    expect((await state()).Articles).toHaveLength(2)
    expect(errors).toEqual([])
    const output = path.resolve(path.dirname(readyFile!), process.env.ROUND_STAGE || 'native')
    mkdirSync(output, { recursive: true })
    writeFileSync(path.join(output,'real-file-'+scenario+'.json'), JSON.stringify({ Start: started,
      End: new Date().toISOString(), Scenario: scenario, JWTValidated: false, Simulation: 'explicit local provider HTTP',
      Ready: ready, Rejected: rejected, Committed: final, Saved: saved, Final: await state(), PageErrors: errors },null,2))
  })
}
