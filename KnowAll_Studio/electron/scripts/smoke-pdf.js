// Actual packaged renderer/gateway and real PDF worker, deterministic local account/files.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const http = require('node:http')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

async function main() {
  const root = path.resolve(__dirname, '..'), output = path.resolve(root, '../docs/open-source-integration')
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round08/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-pdf-'))
  const files = {}
  for (const name of ['textbook', 'short', 'cjk']) files[name] = await fs.readFile(path.join(output, 'round-08-fixtures', `${name}.pdf`))
  const requests = [], assets = [], nativeWorkers = [], resourceResponses = [], browserErrors = [], token = 'pdf-test-token'
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'pdf-user', username: 'pdf', email: 'pdf@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    const match = /^\/api\/v1\/documents\/pdf-(textbook|short|cjk|broken)(\/chunks|\/raw)?$/.exec(req.url)
    if (match) {
      if (req.headers.authorization !== `Bearer ${token}`) { res.statusCode = 401; return res.end('{}') }
      const name = match[1]
      if (match[2] === '/chunks') return res.end(JSON.stringify({ chunks: [{ id: 'section', index: 0, text: 'PDF 学习正文', page_range: 'p1' }] }))
      if (match[2] === '/raw') { res.setHeader('Content-Type', 'application/pdf'); return res.end(files[name] || 'not a PDF') }
      return res.end(JSON.stringify({ id: `pdf-${name}`, filename: `${name}.pdf`, file_type: 'pdf', status: 'ready', total_chunks: 1 }))
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
    await page.getByLabel('账号', { exact: true }).fill('pdf')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') browserErrors.push(m.text()) })
    page.on('pageerror', error => browserErrors.push(error.message))
    page.on('response', r => { if (/pdf\.worker|\/pdfjs\//.test(r.url())) resourceResponses.push({ url: r.url(), status: r.status(), type: r.headers()['content-type'] }) })
    page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/assets/')) assets.push(r.url()) })
    page.on('worker', worker => { nativeWorkers.push(worker.url()); assets.push(worker.url()) })
    page.on('response', r => { if (new URL(r.url()).pathname.startsWith('/pdfjs/')) resourceResponses.push({ url: r.url(), status: r.status() }) })
    const reader = page.locator('.material-pdf'), text = reader.locator('.react-pdf__Page__textContent')
    const pageNumber = page.getByRole('spinbutton', { name: 'PDF 页码', exact: true })
    await page.goto(new URL('/materials/pdf-textbook', page.url()).href)
    await expect(text).toContainText('textbook - Page 1')
    const examples = page.getByRole('link', { name: 'Examples', exact: true })
    await examples.focus(); await page.keyboard.press('Enter')
    await expect(pageNumber).toHaveValue('3'); await expect(text).toContainText('textbook - Page 3')
    await expect(page.getByRole('button', { name: 'PDF 下一页', exact: true })).toBeDisabled()
    await page.getByRole('link', { name: 'Foundations', exact: true }).click()
    await expect(text).toContainText('textbook - Page 1')
    await reader.locator('.react-pdf__Page__annotations .linkAnnotation a').click()
    await expect(pageNumber).toHaveValue('3'); await expect(text).toContainText('textbook - Page 3')
    assert.equal(await page.evaluate(() => localStorage.getItem('knowall:pdf-position:pdf-user:pdf-textbook')), '3')
    await page.screenshot({ path: path.join(output, 'round-08-packaged-pdf.png') })
    const navigate = async name => page.evaluate(name => { history.pushState({}, '', `/materials/pdf-${name}`); window.dispatchEvent(new PopStateEvent('popstate')) }, name)
    await navigate('short'); await expect(text).toContainText('short - Page 1'); await expect(pageNumber).toHaveValue('1')
    await expect(reader).toContainText('此文档没有目录')
    assert.equal(await page.evaluate(() => localStorage.getItem('knowall:pdf-position:pdf-user:pdf-short')), '1')
    await navigate('textbook'); await expect(text).toContainText('textbook - Page 3'); await expect(pageNumber).toHaveValue('3')
    await navigate('broken'); await expect(reader).toContainText('PDF 加载失败'); await expect(pageNumber).toBeDisabled()
    await navigate('cjk'); await expect(text).toContainText('大学学习资料')
    assert.ok(resourceResponses.some(r => /\/cmaps\/UniGB-UCS2-H.bcmap$/.test(r.url) && r.status === 200))
    assert.ok(resourceResponses.some(r => /\/standard_fonts\/LiberationSans-Regular.ttf$/.test(r.url) && r.status === 200))
    assert.ok(resourceResponses.every(r => new URL(r.url).origin === new URL(page.url()).origin))
    await page.evaluate(() => localStorage.setItem('knowall-theme', 'dark')); await page.reload()
    await expect(text).toContainText('大学学习资料')
    await expect(reader.locator('.material-pdf-pages')).toHaveCSS('background-color', 'rgb(14, 29, 42)')
    await page.screenshot({ path: path.join(output, 'round-08-packaged-cjk.png') })
    assert.ok(requests.filter(r => r.url.startsWith('/api/v1/documents/')).every(r => r.authorization === `Bearer ${token}`))
    for (const name of ['textbook', 'short', 'cjk', 'broken']) assert.ok(requests.some(r => r.url === `/api/v1/documents/pdf-${name}/raw`))
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const notices = JSON.parse(await fs.readFile(path.join(output, 'round-08-runtime-notices.json'), 'utf8'))
    let checked = 0
    for (const item of notices) for (const original of item.original_notices) {
      const bytes = await fs.readFile(path.join(resources, 'frontend/third-party', item.name.replace('@', '').replace('/', '-'), original.file))
      assert.equal(sha(bytes), original.sha256); checked++
    }
    const originalResources = JSON.parse(await fs.readFile(path.join(output, 'round-08-fixed-upstream-audit.json'), 'utf8')).resource_originals
    for (const original of originalResources) assert.equal(sha(await fs.readFile(path.join(resources, 'frontend/pdfjs', original.file))), original.sha256)
    for (const name of ['wasm/openjpeg.wasm', 'wasm/qcms_bg.wasm']) {
      const response = await page.request.get(new URL(`/pdfjs/${name}`, page.url()).href)
      assert.equal(response.status(), 200)
      assert.equal(sha(await response.body()), originalResources.find(r => r.file === name).sha256)
    }
    const chunk = assets.find(url => /\/PdfPreview-[^/]+\.js/.test(url)); assert.ok(chunk)
    const worker = assets.find(url => /pdf\.worker\.min-[^/]+\.mjs/.test(url)); assert.ok(worker)
    assert.ok(nativeWorkers.some(url => /pdf\.worker\.min-/.test(url)), 'A real native PDF.js worker must start')
    const workerSha = sha(await fs.readFile(path.join(resources, 'frontend/assets', path.basename(new URL(worker).pathname))))
    assert.equal(workerSha, sha(await fs.readFile(path.join(root, '../frontend/node_modules/pdfjs-dist/build/pdf.worker.min.mjs'))))
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), reader_chunk_sha256: sha(await fs.readFile(path.join(resources, 'frontend/assets', path.basename(new URL(chunk).pathname)))), original_worker_sha256: workerSha, nativeWorkers, observations: { outline_keyboard: true, internal_destination: true, document_positions: true, failed_replacement: true, cjk_native_cmaps_fonts: true, theme: true, bearer_blob: true, original_resource_http: true }, resourceResponses, original_resources_checked: originalResources.length, requests, original_notice_files_checked: checked, passed: true, simulation: 'Actual packaged renderer/gateway/native PDF.js worker with deterministic local account/PDF HTTP fixture; real generated PDFs, not formal production service or all PDF formats.' }
  } catch (error) {
    await fs.writeFile(path.join(output, `round-08-packaged-pdf-diagnostics-${process.env.OSS_EVIDENCE_STAGE || 'current'}.json`), JSON.stringify({ browserErrors, assets, nativeWorkers, resourceResponses, requests }, null, 2))
    throw error
  } finally {
    if (client) await client.close()
    await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir()); await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-08-packaged-pdf.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged PDF: native outline/internal links, private positions, failed recovery, CJK/fonts/worker originals, theme/Bearer/resources/notices/cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
