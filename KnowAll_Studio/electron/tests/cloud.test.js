const assert = require('node:assert/strict')
const test = require('node:test')
const http = require('node:http')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const crypto = require('node:crypto')

test('service origins require HTTPS except loopback and reject embedded credentials or paths', () => {
  const { normalizeServerURL } = require('../cloud-gateway')
  assert.equal(normalizeServerURL('https://study.example.com/'), 'https://study.example.com')
  assert.equal(normalizeServerURL('http://127.0.0.1:8123'), 'http://127.0.0.1:8123')
  for (const value of ['http://study.example.com', 'https://user:secret@study.example.com', 'https://study.example.com/api', 'https://study.example.com/?key=secret', 'file:///tmp']) {
    assert.throws(() => normalizeServerURL(value))
  }
})

test('gateway serves native module workers as JavaScript with nosniff preserved', async (t) => {
  const { startGateway } = require('../cloud-gateway')
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-module-worker-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  await fs.writeFile(path.join(directory, 'index.html'), 'KnowAll')
  await fs.writeFile(path.join(directory, 'pdf.worker.mjs'), 'export const version = "test";')
  const gateway = await startGateway({ serverURL: 'http://127.0.0.1:65530', frontendDir: directory })
  t.after(() => gateway.close())
  const response = await fetch(gateway.origin + '/pdf.worker.mjs')
  assert.equal(response.status, 200)
  assert.match(response.headers.get('content-type'), /^text\/javascript(?:;|$)/)
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(await response.text(), 'export const version = "test";')
})

test('gateway serves the bundle and streams authenticated API calls without SPA fallback for APIs', async (t) => {
  const { startGateway } = require('../cloud-gateway')
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-gateway-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  await fs.writeFile(path.join(directory, 'index.html'), '<h1>KnowAll</h1>')
  const requests = []
  const upstream = http.createServer((req, res) => {
    requests.push({ url: req.url, authorization: req.headers.authorization, origin: req.headers.origin })
    if (req.url === '/api/v1/missing') { res.writeHead(404); return res.end('missing') }
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify({ ok: true }))
  })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => upstream.close(resolve)))
  const serverURL = `http://127.0.0.1:${upstream.address().port}`
  const gateway = await startGateway({ serverURL, frontendDir: directory })
  t.after(() => gateway.close())
  assert.equal(await (await fetch(gateway.origin + '/learning')).text(), '<h1>KnowAll</h1>')
  const result = await fetch(gateway.origin + '/api/v1/questions?limit=2', { headers: { Authorization: 'Bearer test-token', Origin: gateway.origin } })
  assert.deepEqual(await result.json(), { ok: true })
  assert.deepEqual(requests[0], { url: '/api/v1/questions?limit=2', authorization: 'Bearer test-token', origin: serverURL })
  assert.equal((await fetch(gateway.origin + '/api/v1/missing')).status, 404)
  assert.equal((await fetch(gateway.origin + '/api/v1/questions', { headers: { Origin: 'https://evil.example' } })).status, 403)
  assert.equal((await fetch(gateway.origin + '/%2e%2e%2fsecret.txt')).status, 403)
  const requestCount = requests.length
  assert.deepEqual(await (await fetch(gateway.origin + '/api/v1/auth/local-session', { method: 'POST' })).json(), { enabled: false })
  assert.equal(requests.length, requestCount, 'desktop never forwards local auto-login')
})

test('missing frontend prevents startup instead of opening an empty window', async () => {
  const { startGateway } = require('../cloud-gateway')
  await assert.rejects(async () => {
    const gateway = await startGateway({ serverURL: 'https://example.com', frontendDir: path.join(os.tmpdir(), 'knowall-no-such-ui') })
    await gateway.close()
  })
})

test('health validation fails closed and connection config contains no credentials', async (t) => {
  const { checkServer, saveConnection, loadConnection } = require('../cloud-gateway')
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-config-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  const upstream = http.createServer((req, res) => { res.setHeader('content-type', 'application/json'); res.end('{"status":"ok"}') })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => upstream.close(resolve)))
  const url = `http://127.0.0.1:${upstream.address().port}`
  await checkServer(url)
  saveConnection(directory, { serverURL: url })
  assert.equal(loadConnection(directory).serverURL, url)
  await assert.rejects(() => checkServer('http://127.0.0.1:1', 100))
  assert.throws(() => saveConnection(directory, { serverURL: 'https://user:secret@example.com' }))
})

test('gateway proxies authenticated WebSocket upgrade and bidirectional messages', async (t) => {
  const { startGateway } = require('../cloud-gateway')
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-ws-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  await fs.writeFile(path.join(directory, 'index.html'), 'KnowAll')
  let receivedURL
  const upstream = http.createServer()
  const openSockets = new Set()
  upstream.on('connection', socket => { openSockets.add(socket); socket.on('close', () => openSockets.delete(socket)) })
  upstream.on('upgrade', (req, socket) => {
    receivedURL = req.url
    const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n')
    socket.on('data', frame => {
      if ((frame[0] & 15) !== 1) return socket.end()
      const length = frame[1] & 127
      const mask = frame.subarray(2, 6)
      const message = Buffer.from(frame.subarray(6, 6 + length))
      for (let i = 0; i < length; i++) message[i] ^= mask[i % 4]
      const result = Buffer.from('echo:' + message.toString())
      socket.write(Buffer.concat([Buffer.from([0x81, result.length]), result]))
    })
  })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => { for (const socket of openSockets) socket.destroy(); return new Promise(resolve => upstream.close(resolve)) })
  const gateway = await startGateway({ serverURL: `http://127.0.0.1:${upstream.address().port}`, frontendDir: directory })
  t.after(() => gateway.close())
  const ws = new WebSocket(gateway.origin.replace('http:', 'ws:') + '/ws/sync?token=test-token')
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  const message = new Promise(resolve => { ws.onmessage = event => resolve(event.data) })
  ws.send('hello')
  assert.equal(await message, 'echo:hello')
  assert.equal(receivedURL, '/ws/sync?token=test-token')
  ws.close()
})

test('gateway blocks upstream redirects from leaking bearer tokens to other origins', async (t) => {
  const { startGateway } = require('../cloud-gateway')
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-redirect-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  await fs.writeFile(path.join(directory, 'index.html'), 'KnowAll')
  const upstream = http.createServer((req, res) => { res.writeHead(302, { Location: req.url === '/api/malformed' ? 'https://[' : 'https://other.example/api' }); res.end() })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => upstream.close(resolve)))
  const gateway = await startGateway({ serverURL: `http://127.0.0.1:${upstream.address().port}`, frontendDir: directory })
  t.after(() => gateway.close())
  assert.equal((await fetch(gateway.origin + '/api/redirect', { headers: { Authorization: 'Bearer token' } })).status, 502)
  assert.equal((await fetch(gateway.origin + '/api/malformed')).status, 502)
})

test('an interrupted health response is a recoverable connection error', async (t) => {
  const { checkServer } = require('../cloud-gateway')
  const upstream = http.createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'application/json', 'content-length': '100' })
    res.write('{"status":')
    setImmediate(() => res.destroy())
  })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => upstream.close(resolve)))
  await assert.rejects(() => checkServer(`http://127.0.0.1:${upstream.address().port}`))
})

test('gateway preserves streamed upload body, authorization and multipart headers', async (t) => {
  const { startGateway } = require('../cloud-gateway')
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-upload-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  await fs.writeFile(path.join(directory, 'index.html'), 'KnowAll')
  const payload = Buffer.alloc(131072, 65)
  const upstream = http.createServer((req, res) => {
    const hash = crypto.createHash('sha256')
    req.on('data', chunk => hash.update(chunk))
    req.on('end', () => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify({ hash: hash.digest('hex'), auth: req.headers.authorization, type: req.headers['content-type'] })) })
  })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => upstream.close(resolve)))
  const gateway = await startGateway({ serverURL: `http://127.0.0.1:${upstream.address().port}`, frontendDir: directory })
  t.after(() => gateway.close())
  const response = await fetch(gateway.origin + '/api/v1/documents/upload', { method: 'POST', body: payload, headers: { Authorization: 'Bearer uploader', 'Content-Type': 'multipart/form-data; boundary=knowall' } })
  assert.deepEqual(await response.json(), { hash: crypto.createHash('sha256').update(payload).digest('hex'), auth: 'Bearer uploader', type: 'multipart/form-data; boundary=knowall' })
})
