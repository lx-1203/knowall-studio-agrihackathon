// Real packaged renderer + gateway + incremental HTTP, deterministic model output.
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
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-stream-'))
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round02/win-unpacked/KnowAll Studio.exe')
  const requests = [], timers = new Set()
  let client, report
  const server = http.createServer((req, res) => {
    requests.push({ url: req.url, method: req.method, authorization: req.headers.authorization })
    res.setHeader('Content-Type', 'application/json')
    const user = { id: 'stream-user', username: 'stream', email: 'stream@example.test', is_active: true }
    if (req.url === '/health') return res.end('{"status":"ok"}')
    if (req.url === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: 'stream-test-token', user }))
    if (req.url === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    if (req.url === '/api/v1/chat/roles') return res.end('{"tutor":{"name":"助教"}}')
    if (req.url === '/api/v1/chat/assistant/stream') {
      assert.equal(req.headers.authorization, 'Bearer stream-test-token')
      res.setHeader('Content-Type', 'text/event-stream')
      const bytes = Buffer.from(': heartbeat\r\ndata:{"token":"真实分片中文",\r\ndata:"done":false}\r\n\r\ndata:{"done":true,"conversation_id":"real-stream-conv"}\r\n\r\n')
      let index = 0
      const timer = setInterval(() => {
        if (index < bytes.length) res.write(bytes.subarray(index, ++index))
        else { clearInterval(timer); timers.delete(timer); res.end() }
      }, 2)
      timers.add(timer)
      res.on('close', () => { clearInterval(timer); timers.delete(timer) })
      return
    }
    if (req.url === '/api/v1/chat/assistant') {
      assert.equal(req.headers.authorization, 'Bearer stream-test-token')
      return res.end('{"message":"普通模式通过","conversation_id":"real-stream-conv"}')
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
    await page.getByLabel('账号', { exact: true }).fill('stream')
    await page.getByLabel('密码', { exact: true }).fill('safe-test-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor()
    await page.goto(new URL('/chat', page.url()).href)
    await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('流式测试')
    await page.getByRole('button', { name: /发送/ }).click()
    await expect(page.locator('.chat-markdown')).toHaveText('真实分片中文')
    await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
    await page.getByRole('switch').nth(1).click()
    await page.getByPlaceholder('输入问题，Enter 发送，Shift+Enter 换行...').fill('普通测试')
    await page.getByRole('button', { name: /发送/ }).click()
    await expect(page.locator('.chat-markdown').last()).toHaveText('普通模式通过')
    await page.screenshot({ path: path.join(output, 'round-02-packaged-stream.png') })
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    const license = await fs.readFile(path.join(resources, 'frontend/third-party/eventsource-parser/LICENSE'))
    assert.equal(sha(license), sha(await fs.readFile(path.resolve(root, '../frontend/node_modules/eventsource-parser/LICENSE'))))
    assert.equal(requests.filter(r => r.url === '/api/v1/chat/assistant/stream').length, 1)
    assert.equal(requests.filter(r => r.url === '/api/v1/chat/assistant').length, 1)
    report = { at: new Date().toISOString(), executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), license_sha256: sha(license), requests, simulation: 'Deterministic local HTTP response only; actual packaged UI and desktop gateway used. No external model.' }
  } finally {
    if (client) await client.close()
    for (const timer of timers) clearInterval(timer)
    await new Promise(resolve => server.close(resolve))
    await fs.rm(data, { recursive: true, force: true })
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'round-02-packaged-stream.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual packaged SSE: byte-sliced Chinese/multiline CRLF, bearer on both modes, one request each, upstream license included, temporary data removed')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
