// Exercise real packaged renderer/gateway with a local HTTP server and real OOXML.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const http = require('node:http')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const { pathToFileURL } = require('node:url')

async function main() {
  const root = path.resolve(__dirname, '..')
  const output = path.resolve(root, '../docs/open-source-integration')
  const { docxFixture } = await import(pathToFileURL(path.resolve(root, '../frontend/tests/e2e/docx-fixture.ts')).href)
  const bytes = await docxFixture('安装包文档正文')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-docx-'))
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round03/win-unpacked/KnowAll Studio.exe')
  const requests = []
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'docx-user', username: 'docx', email: 'docx@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: 'docx-test-token', user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    if (req.url === '/api/v1/documents/docx-fixture') return res.end(JSON.stringify({ id: 'docx-fixture', filename: 'biology.docx', file_type: 'docx', status: 'ready', total_chunks: 1 }))
    if (req.url === '/api/v1/documents/docx-fixture/chunks') return res.end(JSON.stringify({ chunks: [{ id: 'section', index: 0, text: '正文备用', page_range: 'p1' }] }))
    if (req.url === '/api/v1/documents/docx-fixture/raw') {
      if (req.headers.authorization !== 'Bearer docx-test-token') { res.statusCode = 401; return res.end('{}') }
      res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
      return res.end(bytes)
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
    await page.getByLabel('账号', { exact: true }).fill('docx')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    await page.goto(new URL('/materials/docx-fixture', page.url()).href)
    await expect(page.locator('.docx-preview strong')).toHaveText('安装包文档正文')
    await expect(page.locator('.docx-preview table')).toContainText('ATP')
    await expect(page.locator('.docx-preview a[href^="javascript:"]')).toHaveCount(0)
    await expect(page.locator('.docx-preview a').filter({ hasText: '危险链接' })).toHaveCount(1)
    await expect(page.locator('.docx-preview a').filter({ hasText: '正常链接' })).toHaveAttribute('href', 'https://example.com/lesson')
    await expect.poll(() => page.locator('.docx-preview img').evaluate(img => img.naturalWidth)).toBe(1)
    const raw = requests.filter(r => r.url === '/api/v1/documents/docx-fixture/raw')
    assert.equal(raw.length, 1)
    assert.equal(raw[0].authorization, 'Bearer docx-test-token')
    await page.screenshot({ path: path.join(output, 'round-03-packaged-docx.png') })
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = content => crypto.createHash('sha256').update(content).digest('hex')
    const licenses = {}
    for (const [library, names] of [['dompurify', ['LICENSE', 'LICENSE-MPL']], ['mammoth', ['LICENSE']]]) {
      for (const name of names) {
        const packaged = await fs.readFile(path.join(resources, 'frontend/third-party', library, name))
        assert.equal(sha(packaged), sha(await fs.readFile(path.resolve(root, '../frontend/node_modules', library, name))))
        licenses[`${library}/${name}`] = sha(packaged)
      }
    }
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), fixture_sha256: sha(bytes), licenses, requests, simulation: 'Local HTTP account/file responses; real OOXML, packaged Mammoth/DOMPurify, renderer and desktop gateway. No external model.' }
  } finally {
    if (client) await client.close()
    await new Promise(resolve => server.close(resolve))
    // The path is freshly returned by mkdtemp, inside the OS temporary directory.
    assert.equal(path.dirname(data), os.tmpdir())
    await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-03-packaged-docx.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged DOCX: authorized raw file, table/image/links, unsafe URI removed, original licenses shipped, temporary data removed')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
