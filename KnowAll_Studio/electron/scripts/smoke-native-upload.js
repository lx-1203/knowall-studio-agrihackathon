const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http'), assert = require('node:assert/strict'), crypto = require('node:crypto')
async function main() {
  const root = path.resolve(__dirname, '..'), out = path.resolve(root, '../docs/open-source-integration')
  const exe = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round14/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-native-upload-'))
  const token = 'temporary-native-upload-token', requests = [], aborted = [], timers = new Set(), documents = []
  let client, page, cdp, report, uploads = 0, indexes = 0, imports = 0, urlReady = false
  const document = id => ({ id, filename: `${id}.md`, file_type: 'md', status: 'ready', subject: '生物', material_kind: 'knowledge', page_count: 1, total_chunks: 1, created_at: new Date(0).toISOString() })
  const server = http.createServer((req, res) => {
    const uri = new URL(req.url, 'http://localhost').pathname; res.setHeader('Content-Type', 'application/json')
    const user = { id: 'upload-user', username: 'upload-user', email: 'upload@example.test', is_active: true }
    if (uri === '/health') return res.end('{"status":"ok"}')
    if (uri === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (uri === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    const record = { path: uri, method: req.method, authorization: req.headers.authorization, content_type: req.headers['content-type'], bytes: 0 }; requests.push(record)
    const chunks = []; req.on('data', b => { record.bytes += b.length; chunks.push(b) }); req.on('aborted', () => { record.body_aborted = true })
    if (uri === '/api/v1/documents/upload') { record.upload_number = ++uploads }
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString(), respond = (value, status = 200) => { res.statusCode = status; res.end(JSON.stringify(value)) }
      if (uri.startsWith('/api/v1/documents/') || uri.startsWith('/api/v1/search/')) {
        if (req.headers.authorization !== `Bearer ${token}`) return respond({}, 401)
        if (uri === '/api/v1/documents/upload') {
          record.contains_original = body.includes('原始上传正文'); record.multipart_file_field = body.includes('name="file"')
          const id = `uploaded-${record.upload_number}`
          const timer = setTimeout(() => { timers.delete(timer); documents.push(document(id)); respond({ document_id: id, filename: `${id}.md`, total_chunks: 1, status: 'ready' }) }, 2000); timers.add(timer)
          return
        }
        if (uri === '/api/v1/documents/import-url') {
          imports++; record.body = JSON.parse(body)
          if (!urlReady) return respond({ detail: 'temporary URL import failure' }, 503)
          documents.push(document('url-doc')); return respond({ document_id: 'url-doc', total_chunks: 1 })
        }
        if (uri.endsWith('/organization')) { record.body = JSON.parse(body); return respond(document(uri.split('/').at(-2))) }
        if (uri === '/api/v1/search/index') { indexes++; return respond({ indexed: 1 }) }
        if (uri === '/api/v1/documents/') return respond(documents)
        if (uri.endsWith('/chunks')) return respond([{ id: 'chunk', chunk_index: 0, text: '# 原始上传正文\n从账号资料阅读。', metadata: {} }])
        if (uri.endsWith('/raw')) { res.setHeader('Content-Type', 'text/markdown; charset=utf-8'); return res.end('# 原始上传正文\n从账号资料阅读。') }
        if (/\/documents\/uploaded-\d+$/.test(uri)) return respond(document(uri.split('/').at(-1)))
      }
      respond([])
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }; delete env.KNOWALL_SERVER_URL; delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath: exe, env, timeout: 30000 })
    const settings = await client.firstWindow(); await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window'); await settings.locator('#submit').click(); page = await opened
    await page.getByLabel('账号', { exact: true }).fill('upload-user'); await page.getByLabel('密码', { exact: true }).fill('temporary-fixture-only')
    await page.getByRole('button', { name: '登录', exact: true }).click(); await page.locator('.product-shell').waitFor(); await page.setViewportSize({ width: 1600, height: 950 })
    page.on('requestfailed', r => aborted.push({ url: r.url(), failure: r.failure() }))
    cdp = await page.context().newCDPSession(page); await cdp.send('Network.enable')
    const throttle = enabled => cdp.send('Network.emulateNetworkConditions', { offline: false, latency: enabled ? 20 : 0, downloadThroughput: enabled ? 10 * 1024 * 1024 : -1, uploadThroughput: enabled ? 64 * 1024 : -1 })
    const origin = new URL(page.url()).origin, go = route => page.goto(`${origin}${route}`)
    const buffer = Buffer.alloc(512 * 1024, 'X'); Buffer.from('# 原始上传正文\n').copy(buffer)
    const big = { name: 'native-account.md', mimeType: 'text/markdown', buffer }
    const small = { ...big, buffer: Buffer.from('# 原始上传正文\nUTF-8 账号资料。') }
    const measured = []
    for (const legacy of [false, true]) {
      await go(legacy ? '/upload' : '/materials')
      if (!legacy) { await page.getByRole('button', { name: /上传资料$/ }).first().click(); await page.getByLabel('上传资料科目', { exact: true }).fill('生物') }
      await throttle(true); const before = uploads
      await page.locator('input[type=file]').setInputFiles(big)
      const progress = page.getByLabel('文件上传进度', { exact: true })
      await expect.poll(async () => { const text = await progress.textContent().catch(() => ''); const n = Number.parseInt(text); return n > 0 && n < 100 }).toBe(true)
      const percent = Number.parseInt(await progress.textContent()); measured.push({ entry: legacy ? 'advanced' : 'primary', real_native_partial_percent: percent })
      await expect.poll(() => uploads).toBe(before + 1)
      await page.screenshot({ path: path.join(out, `round-14-packaged-${legacy ? 'advanced' : 'primary'}-progress.png`), animations: 'disabled' })
      const beforeAbort = aborted.length; await page.getByRole('button', { name: '停止当前上传', exact: true }).click()
      await expect.poll(() => aborted.slice(beforeAbort).some(r => r.url.endsWith('/documents/upload'))).toBe(true)
      await expect(page.getByText(/已停止等待/)).toBeVisible(); assert.equal(uploads, before + 1)
      assert.equal(requests.filter(r => r.path.endsWith('/organization')).length, legacy ? 1 : 0)
      assert.equal(indexes, 0)
      await throttle(false); await page.locator('input[type=file]').setInputFiles(small)
      await expect(page.getByText('文件已传输，正在等待解析结果。', { exact: true })).toBeVisible()
      await expect(progress).toContainText('100% 已传输')
      assert.equal(uploads, before + 2)
      if (legacy) { await expect.poll(() => indexes).toBe(1); await expect(page.getByText('已自动建立向量索引', { exact: true })).toBeVisible() }
      else { await expect(page).toHaveURL(/\/materials\/uploaded-2$/); await expect(page.getByRole('heading', { name: 'uploaded-2.md', exact: true })).toBeVisible() }
    }
    assert.equal(uploads, 4)
    const completed = requests.filter(r => r.upload_number && !r.body_aborted)
    assert.equal(completed.length, 2); assert.ok(completed.every(r => r.contains_original && r.multipart_file_field && r.content_type.startsWith('multipart/form-data; boundary=')))
    assert.deepEqual(requests.find(r => r.path.endsWith('/organization')).body, { subject: '生物', material_kind: 'knowledge' })
    await page.getByRole('button', { name: /导入URL$/ }).click(); await page.getByPlaceholder('https://example.com/article').fill('https://example.test/中文?a=1&b=%20')
    await page.getByRole('button', { name: /导\s*入$/, exact: true }).click()
    await expect(page.getByRole('dialog').getByRole('alert')).toContainText('导入失败'); assert.equal(imports, 1)
    await expect(page.getByPlaceholder('https://example.com/article')).toHaveValue('https://example.test/中文?a=1&b=%20')
    urlReady = true; await page.getByRole('button', { name: /导\s*入$/, exact: true }).evaluate(el => { el.click(); el.click() })
    await expect(page.getByRole('dialog')).toBeHidden(); assert.equal(imports, 2)
    assert.ok(requests.filter(r => r.path.startsWith('/api/v1/documents/') || r.path.startsWith('/api/v1/search/')).every(r => r.authorization === `Bearer ${token}`))
    assert.ok(requests.filter(r => r.path.endsWith('/import-url')).every(r => r.body.url === 'https://example.test/中文?a=1&b=%20'))
    const sha = b => crypto.createHash('sha256').update(b).digest('hex'), resources = path.join(path.dirname(exe), 'resources')
    const notices = JSON.parse(await fs.readFile(path.join(out, 'round-14-runtime-notices.json'), 'utf8')); let checked = 0
    for (const item of notices) for (const n of item.original_notices) { assert.equal(sha(await fs.readFile(path.join(resources, 'frontend/third-party', item.name.replace('@', '').replace('/', '-'), n.file))), n.sha256); checked++ }
    report = { at: new Date().toISOString(), executable: exe, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), original_notice_files_checked: checked, passed: true, requests, aborted, measured, uploads, imports, indexes, observations: { real_native_progress_two_entries: true, native_cdp_throttled_http_body_not_synthetic_progress: true, transfer_100_not_parse_completion: true, cancellation_and_manual_recovery: true, no_cancelled_followups: true, url_bearer_and_manual_only_one_write: true, source_classification_multipart_preserved: true }, simulation: 'Current actual packaged renderer/gateway/local HTTP server; CDP network throttling and real multipart bytes/native XMLHttpRequest progress; no progress injection. Temporary deterministic accounts/content/URL handler (no external URL fetch or paid models); actual cloud workers separately tested.' }
  } catch (error) {
    await fs.writeFile(path.join(out, 'round-14-packaged-native-upload-diagnostics.json'), JSON.stringify({ url: page?.url(), snapshot: page ? await page.locator('body').ariaSnapshot() : '', requests, aborted, uploads, imports, indexes }, null, 2)); throw error
  } finally {
    for (const timer of timers) clearTimeout(timer)
    if (client) await client.close()
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir()); await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true; await fs.writeFile(path.join(out, 'round-14-packaged-native-upload.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged native HTTP upload progress/cancel/recovery/classification/multipart/URL account/post once/original notices/cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
