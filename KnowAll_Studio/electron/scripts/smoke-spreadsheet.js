// Actual packaged reader/gateway; deterministic local account and binary fixtures, no model.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const XLSX = require('../../frontend/node_modules/xlsx')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const http = require('node:http')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

function workbook(type) {
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([
    ['学科', '学科', '', 123, '_key', '__proto__'],
    ['生物', '化学', '空头值', '数字头', '原键值', '<script>原文</script>'],
  ]), '课程表')
  const formatted = XLSX.utils.aoa_to_sheet([['比例', '日期'], [0.25, 45292]])
  formatted.A2.z = '0.00%'
  formatted.B2.z = 'yyyy-mm-dd'
  XLSX.utils.book_append_sheet(wb, formatted, '格式')
  return XLSX.write(wb, { type: 'buffer', bookType: type === 'xls' ? 'biff8' : 'xlsx' })
}

async function main() {
  const root = path.resolve(__dirname, '..')
  const output = path.resolve(root, '../docs/open-source-integration')
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round05/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-spreadsheet-'))
  const requests = [], observations = {}
  const sources = { xlsx: workbook('xlsx'), xls: workbook('xls'), csv: Buffer.from('\ufeff编号;说明;数值;日期\r\n00123;"中文;\n第二行";1e3;2024-01-01\r\n') }
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'sheet-user', username: 'sheet', email: 'sheet@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: 'sheet-test-token', user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    const match = /^\/api\/v1\/documents\/sheet-(xlsx|xls|csv)(\/chunks|\/raw)?$/.exec(req.url)
    if (match) {
      const type = match[1]
      if (match[2] === '/chunks') return res.end(JSON.stringify({ chunks: [{ id: 'section', index: 0, text: '学习资料课程表', page_range: 'p1' }] }))
      if (match[2] === '/raw') {
        if (req.headers.authorization !== 'Bearer sheet-test-token') { res.statusCode = 401; return res.end('{}') }
        res.setHeader('Content-Type', 'application/octet-stream')
        return res.end(sources[type])
      }
      return res.end(JSON.stringify({ id: `sheet-${type}`, filename: `lesson.${type}`, file_type: type, status: 'ready', total_chunks: 1 }))
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
    await page.getByLabel('账号', { exact: true }).fill('sheet')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    const assets = []
    page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/assets/')) assets.push(r.url()) })
    for (const type of ['xlsx', 'xls', 'csv']) {
      const start = assets.length
      await page.goto(new URL(`/materials/sheet-${type}`, page.url()).href)
      const cells = page.locator('.ant-tabs-tabpane-active tr[data-row-key]').first().locator('td')
      const expected = type === 'csv' ? ['00123', '中文;\n第二行', '1e3', '2024-01-01'] : ['生物', '化学', '空头值', '数字头', '原键值', '<script>原文</script>']
      await expect(cells).toHaveText(expected)
      await expect(page.locator('.ant-table script, .ant-table img')).toHaveCount(0)
      const values = await cells.allTextContents()
      if (type !== 'csv') {
        await page.getByRole('tab', { name: '格式', exact: true }).click()
        await expect(cells).toHaveText(['25.00%', '2024-01-01'])
      }
      const requested = assets.slice(start)
      assert.equal(requested.filter(url => /\/cpexcel\.full-[^/]+\.js/.test(url)).length, type === 'xls' ? 1 : 0)
      const raw = requests.filter(r => r.url === `/api/v1/documents/sheet-${type}/raw`)
      assert.equal(raw.length, 1)
      assert.equal(raw[0].authorization, 'Bearer sheet-test-token')
      observations[type] = { values, assets: requested, formatted: type !== 'csv' ? await cells.allTextContents() : null }
      if (type === 'xlsx') await page.screenshot({ path: path.join(output, 'round-05-packaged-spreadsheet.png') })
    }
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const provenance = JSON.parse(await fs.readFile(path.join(output, 'round-05-upstream.json'), 'utf8'))
    const notices = {}
    for (const item of provenance.original_notices) {
      const bytes = await fs.readFile(path.join(resources, 'frontend/third-party/xlsx', item.file))
      assert.equal(sha(bytes), item.sha256)
      notices[item.file] = sha(bytes)
    }
    assert.equal(provenance.version, '0.20.3')
    const previewAsset = observations.xlsx.assets.find(url => /\/XlsxPreview-[^/]+\.js/.test(url))
    assert.ok(previewAsset)
    const bundle = await fs.readFile(path.join(resources, 'frontend/assets', path.basename(new URL(previewAsset).pathname)), 'utf8')
    // Rollup removes the unused version export. Version is proved by pinned original
    // source/lock hashes and the audited current build, not a retained marker string.
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), frontend_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))), reader_chunk_sha256: sha(Buffer.from(bundle)), observations, requests, notices, parser_version: provenance.version, parser_version_evidence: 'Fixed official source/lock integrity and audited current-source build; unused version export tree-shaken.', passed: true, simulation: 'Local deterministic account/file HTTP fixture with actual packaged reader/gateway. No external model or formal production endpoint.' }
  } finally {
    if (client) await client.close()
    await new Promise(resolve => server.close(resolve))
    assert.equal(path.dirname(data), os.tmpdir())
    await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-05-packaged-spreadsheet.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged spreadsheet: XLSX/XLS/CSV cells/format/source, native legacy codepage only for XLS, Bearer, Apache originals, parser version, cleanup')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
