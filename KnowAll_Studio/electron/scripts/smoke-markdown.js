// Actual packaged renderer and gateway; local HTTP fixtures, no paid model.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const http = require('node:http')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

async function main() {
  const root = path.resolve(__dirname, '..'), output = path.resolve(root, '../docs/open-source-integration')
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round06/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-markdown-'))
  const requests = [], assets = []
  const sources = {
    md: '# 学习公式\n\n行内 $x^2$，价格 \\$5。\n\n$$\n\\frac{1}{2}\n$$\n\n```py\ndef study(value):\n    return value + 1\n```\n\n`$source$`\n\n|学科|进度|\n|---|---|\n|数学|50%|\n\n- [x] 已学习\n\n<script>window.mdExecuted=1</script>\n\n[危险](javascript:alert(1))\n\n$\\htmlClass{owned}{bad}$\n',
    txt: '# 原文\n$x$\n<script>不能执行</script>\n',
  }
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'markdown-user', username: 'markdown', email: 'markdown@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: 'markdown-test-token', user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    const match = /^\/api\/v1\/documents\/markdown-(md|txt)(\/chunks|\/raw)?$/.exec(req.url)
    if (match) {
      const type = match[1]
      if (match[2] === '/chunks') return res.end(JSON.stringify({ chunks: [{ id: 'section', index: 0, text: '学习公式原文', page_range: 'p1' }] }))
      if (match[2] === '/raw') {
        if (req.headers.authorization !== 'Bearer markdown-test-token') { res.statusCode = 401; return res.end('{}') }
        res.setHeader('Content-Type', 'text/plain; charset=utf-8'); return res.end(sources[type])
      }
      return res.end(JSON.stringify({ id: `markdown-${type}`, filename: type === 'md' ? '无后缀学习资料' : 'source.md', file_type: type, status: 'ready', total_chunks: 1 }))
    }
    res.end('[]')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }
    delete env.KNOWALL_SERVER_URL; delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath, env, timeout: 30000 })
    const settings = await client.firstWindow()
    await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window'); await settings.locator('#submit').click(); const page = await opened
    await page.getByLabel('账号', { exact: true }).fill('markdown')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/assets/')) assets.push(r.url()) })
    await page.goto(new URL('/materials/markdown-md', page.url()).href)
    const reader = page.locator('.markdown-preview')
    // The untrusted command also produces native readable math output.
    await expect(reader.locator('.katex')).toHaveCount(3)
    await expect(reader.locator('.katex-display')).toHaveCount(1)
    await expect(reader.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(166, 38, 164)')
    await expect(reader.locator('pre code').last()).toHaveText('def study(value):\n    return value + 1\n')
    await expect(reader.locator('p > code')).toHaveText('$source$')
    await expect(reader.locator('table td')).toHaveText(['数学', '50%'])
    await expect(reader.locator('input[type=checkbox]')).toBeChecked()
    await expect(reader).toContainText('价格 $5')
    await expect(reader.locator('script, img, .owned, a[href^="javascript:"]')).toHaveCount(0)
    assert.equal(await page.evaluate(() => window.mdExecuted), undefined)
    assert.ok(assets.some(url => /\/python-[^/]+\.js/.test(url)), 'Native Python grammar must be requested')
    assert.equal(assets.some(url => /\/(tsx|jsx)-[^/]+\.js/.test(url)), false)
    const initialAssets = [...assets]
    await page.screenshot({ path: path.join(output, 'round-06-packaged-markdown.png') })
    await page.evaluate(() => localStorage.setItem('knowall-theme', 'dark')); await page.reload()
    await expect(reader).toHaveCSS('background-color', 'rgb(20, 38, 54)')
    await expect(reader.locator('pre code span').filter({ hasText: /^def$/ }).first()).toHaveCSS('color', 'rgb(86, 156, 214)')
    await page.goto(new URL('/materials/markdown-txt', page.url()).href)
    await expect(reader.locator('pre')).toHaveText(sources.txt)
    await expect(reader.locator('h1, .katex, script')).toHaveCount(0)
    for (const type of ['md','txt']) {
      const raw = requests.filter(r => r.url === `/api/v1/documents/markdown-${type}/raw`)
      assert.ok(raw.length >= 1)
      assert.ok(raw.every(r => r.authorization === 'Bearer markdown-test-token'))
    }
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const notices = JSON.parse(await fs.readFile(path.join(output, 'round-06-runtime-notices.json'), 'utf8'))
    let checked = 0
    for (const item of notices) for (const original of item.original_notices) {
      const bytes = await fs.readFile(path.join(resources, 'frontend/third-party', item.name.replace('@','').replace('/','-'), original.file))
      assert.equal(sha(bytes), original.sha256); checked++
    }
    const chunk = initialAssets.find(url => /\/MarkdownPreview-[^/]+\.js/.test(url)); assert.ok(chunk)
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), reader_chunk_sha256: sha(await fs.readFile(path.join(resources, 'frontend/assets', path.basename(new URL(chunk).pathname)))), observations: { math: true, gfm: true, code_source: true, light_dark: true, safe_html_macro: true, metadata_type: true, txt: true, native_grammar_assets: initialAssets }, requests, original_notice_files_checked: checked, passed: true, simulation: 'Actual packaged renderer/gateway with deterministic local account/file HTTP fixture; no formal production service.' }
  } finally {
    if (client) await client.close()
    await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir()); await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-06-packaged-markdown.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged Markdown: native formulas/GFM/code/color, metadata/TXT, security, only needed grammar, Bearer, original notices, cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
