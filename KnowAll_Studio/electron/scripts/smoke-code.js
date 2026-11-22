// Real packaged code reader/gateway; local HTTP account/file fixture, no model.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const http = require('node:http')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

async function main() {
  const root = path.resolve(__dirname, '..')
  const output = path.resolve(root, '../docs/open-source-integration')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-code-'))
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round04/win-unpacked/KnowAll Studio.exe')
  const source = 'def greet(name):\n    message = "你好，学习者"\n    return message\n'
  const requests = [], assets = []
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'code-user', username: 'code', email: 'code@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: 'code-test-token', user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    if (req.url === '/api/v1/documents/code-fixture') return res.end(JSON.stringify({ id: 'code-fixture', filename: 'lesson.py', file_type: 'py', status: 'ready', total_chunks: 1 }))
    if (req.url === '/api/v1/documents/code-fixture/chunks') return res.end(JSON.stringify({ chunks: [{ id: 'section', index: 0, text: source, page_range: 'p1' }] }))
    if (req.url === '/api/v1/documents/code-fixture/raw') {
      if (req.headers.authorization !== 'Bearer code-test-token') { res.statusCode = 401; return res.end('{}') }
      res.setHeader('Content-Type', 'text/plain; charset=utf-8')
      return res.end(source)
    }
    return res.end('[]')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }
    delete env.KNOWALL_SERVER_URL
    delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath, env, timeout: 30000 })
    const settings = await client.firstWindow()
    await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window')
    await settings.locator('#submit').click()
    const page = await opened
    await page.getByLabel('账号', { exact: true }).fill('code')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/assets/')) assets.push(r.url()) })
    await page.goto(new URL('/materials/code-fixture', page.url()).href)
    const code = page.locator('.code-preview pre code').last()
    const original = () => code.evaluate(node => {
      const copy = node.cloneNode(true)
      copy.querySelectorAll('.react-syntax-highlighter-line-number').forEach(line => line.remove())
      return copy.textContent
    })
    await expect.poll(original).toBe(source)
    const keyword = code.locator('.token').filter({ hasText: /^def$/ }).first()
    await expect(keyword).toBeVisible()
    const colors = await keyword.evaluate(node => ({ token: getComputedStyle(node).color, base: getComputedStyle(node.closest('code')).color }))
    assert.notEqual(colors.token, colors.base)
    await expect(page.locator('.react-syntax-highlighter-line-number').first()).toHaveText('1')
    assert.equal(assets.filter(url => /\/python-[^/]+\.js/.test(url)).length, 1)
    assert.equal(assets.filter(url => /\/(?:tsx|java|rust|go|sql|cpp)-[^/]+\.js/.test(url)).length, 0)
    const normalAssets = [...assets]
    assert.equal(requests.filter(r => r.url === '/api/v1/documents/code-fixture/raw').length, 1)
    await page.screenshot({ path: path.join(output, 'round-04-packaged-code.png') })
    let blocked = 0
    await page.route(/\/assets\/python-[^/]+\.js(?:\?.*)?$/, r => { blocked++; return r.abort('failed') })
    await page.reload()
    await expect.poll(() => blocked).toBeGreaterThan(0)
    await expect.poll(original).toBe(source)
    await expect(code.locator('.token')).toHaveCount(0)
    assert.ok(requests.filter(r => r.url === '/api/v1/documents/code-fixture/raw').every(r => r.authorization === 'Bearer code-test-token'))
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const notices = JSON.parse(await fs.readFile(path.join(output, 'round-04-runtime-notices.json'), 'utf8'))
    const licenses = {}
    for (const item of notices) {
      const library = item.name.replaceAll('/', '-').replaceAll('@', '')
      for (const notice of item.original_notices) {
        const bytes = await fs.readFile(path.join(resources, 'frontend/third-party', library, notice.file))
        assert.equal(sha(bytes), notice.sha256)
        licenses[`${library}/${notice.file}`] = sha(bytes)
      }
    }
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), source_sha256: sha(Buffer.from(source)), keyword_colors: colors, normal_assets: normalAssets, grammar_failure_plaintext: blocked > 0, licenses, requests, simulation: 'Local HTTP account/file fixture, real packaged reader/gateway and native async Prism. No external model or production endpoint.' }
  } finally {
    if (client) await client.close()
    await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir())
    await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-04-packaged-code.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged code: syntax colors/source/lines, only current grammar, plaintext on grammar failure, bearer and original MIT notices, cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
