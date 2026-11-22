import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

const body = '我们通过认真学习理解这个世界，并且每天阅读新的知识内容。'
const modal = (page: Page) => page.getByRole('dialog').filter({ hasText: '上传文章' })
const title = (page: Page) => modal(page).getByPlaceholder('文章标题（必填）')
const content = (page: Page) => modal(page).getByPlaceholder('文章正文...')
const input = (page: Page) => modal(page).locator('input[type=file]')
const hint = (page: Page) => modal(page).getByText(/^已加载:/)
let writes: any[] = []

async function open(page: Page) {
  await page.goto('/reading')
  await page.getByRole('button', { name: '+ 上传文章', exact: true }).click()
  await title(page).fill('已有正确标题')
  await content(page).fill(body)
}
async function choose(page: Page, raw: Buffer, name = 'lesson.txt') {
  // A user clicks the file input, blurring any edited textarea before file parsing.
  await input(page).focus()
  await input(page).setInputFiles({ name, mimeType: 'text/plain', buffer: raw })
}
async function unchanged(page: Page) {
  await expect(title(page)).toHaveValue('已有正确标题')
  await expect(content(page)).toHaveValue(body)
  expect(writes).toEqual([])
  await expect(hint(page)).toHaveCount(0)
}
async function gates(page: Page) {
  await page.addInitScript(() => {
    ;(window as any).byteGates = {}
    const arrayBuffer = File.prototype.arrayBuffer
    const text = File.prototype.text
    for (const method of ['arrayBuffer', 'text'] as const) {
      const original = method === 'arrayBuffer' ? arrayBuffer : text
      ;(File.prototype as any)[method] = function () {
        if (!this.name.startsWith('slow')) return original.call(this)
        const file = this
        return new Promise((resolve, reject) => {
          ;(window as any).byteGates[file.name] = async () => {
            if (file.name.includes('io-error')) reject(new Error('explicit simulated file IO failure'))
            else resolve(await original.call(file))
          }
        })
      }
    }
  })
}
async function release(page: Page, name: string) {
  await expect.poll(() => page.evaluate(name => !!(window as any).byteGates[name], name)).toBe(true)
  await page.evaluate(name => (window as any).byteGates[name](), name)
  await page.evaluate(() => new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
}

test.beforeEach(async ({ page }) => {
  writes = []
  await page.route('**/api/v1/reading/articles', r => r.fulfill({ json: [] }))
  await page.route('**/api/v1/reading/articles/upload', r => {
    const article = r.request().postDataJSON(); writes.push(article)
    return r.fulfill({ json: { ok: true, article: { ...article, id: 'u1', source: 'upload' } } })
  })
  await page.route('**/api/v1/reading/convert/stream', r => r.fulfill({
    contentType: 'text/event-stream', body: 'data: '+JSON.stringify({ done: true, result: '完成 world', vocabulary: [] })+'\n\n',
  }))
})
test.afterEach(async ({ page }, info) => {
  const output = process.env.READING_BYTE_EVIDENCE_DIR
  if (!output) return
  mkdirSync(output, { recursive: true })
  const current = await page.evaluate(() => ({ URL: location.href, Vocab: localStorage.getItem('reading_lang_vocab') })).catch(() => null)
  writeFileSync(path.join(output, info.testId+'.json'), JSON.stringify({ Test: info.title, Status: info.status,
    End: new Date().toISOString(), Simulation: 'page HTTP explicitly mocked; real browser file decoding', Writes: writes, Current: current }, null, 2))
})

for (const [name, separator, bom] of [
  ['plain.txt', '\n', false], ['bom.TXT', '\r\n', true], ['mixed.TxT', '\n', true], ['中文.txt', '\r\n', false],
] as const) {
  test(`valid UTF8/BOM preserves ${name} body bytes`, async ({ page }) => {
    await open(page)
    const text = '文件标题'+separator+body+separator+'第二段 emoji 🌞 café �'
    await choose(page, Buffer.concat([bom ? Buffer.from([0xef,0xbb,0xbf]) : Buffer.alloc(0), Buffer.from(text)]), name)
    await expect(title(page)).toHaveValue('文件标题')
    await expect(content(page)).toHaveValue((body+separator+'第二段 emoji 🌞 café �').replace(/\r\n/g,'\n'))
    await expect(hint(page)).toBeVisible()
    await modal(page).getByRole('button', { name: '保存', exact: true }).click()
    await expect.poll(() => writes.length).toBe(1)
    expect(writes[0]).toEqual({ title: '文件标题', content: body+separator+'第二段 emoji 🌞 café �' })
  })
}

const invalid = {
  gbk: Buffer.concat([Buffer.from('legacy\n'), Buffer.from(Array(25).fill([0xd6,0xd0]).flat())]),
  utf16: Buffer.concat([Buffer.from([0xff,0xfe]), Buffer.from('标题\n'+body,'utf16le')]),
  truncated: Buffer.concat([Buffer.from('标题\n'+body), Buffer.from([0xe4,0xb8])]),
  badMiddle: Buffer.concat([Buffer.from('标题\n'), Buffer.from([0xff]), Buffer.from(body)]),
}
for (const [name, raw] of Object.entries(invalid)) {
  test(`invalid ${name} gives encoding guidance without replacing draft`, async ({ page }) => {
    await open(page); await choose(page, raw)
    await expect(page.getByText('文件编码无效，请另存为 UTF-8 后重新上传', { exact: true })).toBeVisible()
    await unchanged(page)
  })
}

test('valid UTF8 Cyrillic text is not guessed to be another encoding', async ({ page }) => {
  await open(page)
  await choose(page, Buffer.from('多语言标题\n'+body+' ѧϰ'))
  await expect(title(page)).toHaveValue('多语言标题')
  await expect(content(page)).toHaveValue(body+' ѧϰ')
  await expect(hint(page)).toBeVisible()
})
for (const [name, text] of Object.entries({ empty: '', onlyTitle: '标题', onlyWhitespace: ' \n\t',
  blankFirstLine: '\n标题\n'+body, blankCRLF: ' \r\n标题\r\n'+body, bomBlank: '\ufeff\n标题\n'+body })) {
  test(`invalid layout ${name} keeps first line semantics and draft`, async ({ page }) => {
    await open(page); await choose(page, Buffer.from(text))
    await expect(page.getByText('文件格式错误：首行为标题，之后为正文', { exact: true })).toBeVisible()
    await unchanged(page)
  })
}

test('same physical file can be selected again after editing and input releases selection', async ({ page }) => {
  await open(page)
  const bytes = Buffer.from('同文件标题\n'+body)
  await choose(page, bytes, 'repeat.txt')
  await expect(title(page)).toHaveValue('同文件标题')
  await expect(input(page)).toHaveValue('')
  await title(page).fill('临时编辑标题')
  await choose(page, bytes, 'repeat.txt')
  await expect(title(page)).toHaveValue('同文件标题')
  await expect(input(page)).toHaveValue('')
})

test('invalid same filename can recover with valid replacement bytes', async ({ page }) => {
  await open(page); await choose(page, invalid.truncated, 'corrected.txt')
  await expect(page.getByText('文件编码无效，请另存为 UTF-8 后重新上传', { exact: true })).toBeVisible()
  await choose(page, Buffer.from('更正文件标题\n'+body), 'corrected.txt')
  await expect(title(page)).toHaveValue('更正文件标题')
  await expect(hint(page)).toBeVisible()
  await modal(page).getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]).toEqual({ title: '更正文件标题', content: body })
})

test('current local IO rejection preserves draft and can recover', async ({ page }) => {
  await gates(page); await open(page)
  await choose(page, Buffer.from('旧文件\n'+body), 'slow-io-error.txt')
  await release(page, 'slow-io-error.txt')
  await expect(page.getByText('文件读取失败', { exact: true })).toBeVisible()
  await unchanged(page)
  await choose(page, Buffer.from('恢复标题\n'+body), 'good.txt')
  await expect(title(page)).toHaveValue('恢复标题')
})

for (const outcome of ['success', 'encoding', 'io-error'] as const) {
  test(`late file ${outcome} cannot overwrite or notify after a newer selection`, async ({ page }) => {
    await gates(page); await open(page)
    const name = `slow-${outcome}.txt`
    await choose(page, outcome === 'encoding' ? invalid.badMiddle : Buffer.from('旧文件\n'+body), name)
    await choose(page, Buffer.from('新文件标题\n'+body), 'new.txt')
    await expect(title(page)).toHaveValue('新文件标题')
    await release(page, name)
    await expect(title(page)).toHaveValue('新文件标题')
    await expect(page.getByText('文件读取失败', { exact: true })).toHaveCount(0)
    await expect(page.getByText('文件编码无效，请另存为 UTF-8 后重新上传', { exact: true })).toHaveCount(0)
    await expect(hint(page)).toContainText('新文件标题')
  })
}

for (const intent of ['manual-edit', 'close-reopen', 'unmount'] as const) {
  test(`late bad decoding is silent after ${intent}`, async ({ page }) => {
    await gates(page); await open(page); await choose(page, invalid.badMiddle, 'slow-bad.txt')
    if (intent === 'manual-edit') await title(page).fill('新草稿')
    if (intent === 'close-reopen') {
      await modal(page).getByRole('button', { name: '取消', exact: true }).click()
      await page.getByRole('button', { name: '+ 上传文章', exact: true }).click()
      await title(page).fill('新草稿')
    }
    if (intent === 'unmount') {
      await page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
      await expect(page.locator('.reading-main')).toHaveCount(0)
    }
    await release(page, 'slow-bad.txt')
    if (intent !== 'unmount') await expect(title(page)).toHaveValue('新草稿')
    await expect(page.getByText('文件编码无效，请另存为 UTF-8 后重新上传', { exact: true })).toHaveCount(0)
    expect(writes).toEqual([])
  })
}
