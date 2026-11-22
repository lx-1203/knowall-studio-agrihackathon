// A loopback-only origin for the bundled UI. Credentials are forwarded solely
// to the explicitly configured service; no remote code is loaded into Electron.
const http = require('node:http')
const https = require('node:https')
const fs = require('node:fs')
const path = require('node:path')

function normalizeServerURL(value) {
  const url = new URL(String(value).trim())
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) throw new Error('云服务必须使用 HTTPS；仅本机开发允许 HTTP。')
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('请填写服务根地址，不要包含账号、路径或参数。')
  return url.origin
}

function loadConnection(userData) {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(userData, 'connection.json'), 'utf8'))
    return { serverURL: normalizeServerURL(config.serverURL), port: Number.isInteger(config.port) && config.port > 1024 && config.port < 65536 ? config.port : 0 }
  } catch { return null }
}

function saveConnection(userData, { serverURL, port = 0 }) {
  const config = { serverURL: normalizeServerURL(serverURL), port }
  fs.mkdirSync(userData, { recursive: true })
  const filename = path.join(userData, 'connection.json')
  fs.writeFileSync(filename + '.tmp', JSON.stringify(config, null, 2), { mode: 0o600 })
  fs.renameSync(filename + '.tmp', filename)
}

function checkServer(serverURL, timeout = 10000) {
  const origin = normalizeServerURL(serverURL)
  return new Promise((resolve, reject) => {
    const transport = origin.startsWith('https:') ? https : http
    const req = transport.get(origin + '/health', { timeout }, res => {
      let body = ''
      res.on('data', chunk => { body += chunk; if (body.length > 4096) req.destroy(new Error('健康检查响应异常。')) })
      res.on('end', () => {
        try {
          if (res.statusCode !== 200 || JSON.parse(body).status !== 'ok') throw new Error('服务未就绪，请确认 KnowAll 云服务地址。')
          resolve()
        } catch (error) { reject(error) }
      })
      res.on('error', reject)
    })
    req.on('timeout', () => req.destroy(new Error('连接超时，请检查服务地址与网络。')))
    req.on('error', reject)
  })
}

const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }

async function startGateway({ serverURL, frontendDir, port = 0 }) {
  const target = new URL(normalizeServerURL(serverURL))
  const transport = target.protocol === 'https:' ? https : http
  const root = path.resolve(frontendDir)
  if (!fs.existsSync(path.join(root, 'index.html'))) throw new Error('桌面界面资源缺失，请重新安装 KnowAll Studio。')
  const sockets = new Set()
  const upstreams = new Set()
  let origin
  const allowed = req => req.headers.host === new URL(origin).host && (!req.headers.origin || req.headers.origin === origin)
  const proxyHeaders = req => {
    const headers = { ...req.headers, host: target.host }
    // CORS and websocket checks see the configured service origin.
    if (headers.origin) headers.origin = target.origin
    delete headers['proxy-authorization']
    delete headers['proxy-connection']
    delete headers['forwarded']
    for (const key of Object.keys(headers)) if (key.startsWith('x-forwarded-')) delete headers[key]
    return headers
  }
  const server = http.createServer((req, res) => {
    if (!allowed(req)) { res.writeHead(403); return res.end('Forbidden') }
    const rawPath = req.url.split('?')[0]
    if (rawPath === '/api/v1/auth/local-session') {
      res.setHeader('content-type', 'application/json')
      req.resume()
      return res.end(JSON.stringify({ enabled: false }))
    }
    if (rawPath === '/api' || rawPath.startsWith('/api/') || rawPath === '/glossary_terms.kgb') {
      const proxy = transport.request({ hostname: target.hostname.replace(/^\[|\]$/g, ''), port: target.port || undefined, protocol: target.protocol, path: req.url, method: req.method, headers: proxyHeaders(req) }, upstream => {
        const headers = { ...upstream.headers }
        if (headers.location) {
          let location
          try { location = new URL(headers.location, target) } catch {
            res.writeHead(502); upstream.resume(); return res.end('Invalid service redirect')
          }
          if (location.origin !== target.origin || location.username || location.password) { res.writeHead(502); upstream.resume(); return res.end('External redirect blocked') }
          headers.location = location.pathname + location.search + location.hash
        }
        res.writeHead(upstream.statusCode, headers)
        upstream.pipe(res)
        upstream.on('error', () => res.destroy())
      })
      upstreams.add(proxy)
      proxy.once('close', () => upstreams.delete(proxy))
      proxy.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('云服务连接中断，请稍后重试。') })
      proxy.setTimeout(180000, () => proxy.destroy(new Error('Upstream timeout')))
      req.on('aborted', () => proxy.destroy())
      res.on('close', () => { if (!res.writableFinished) proxy.destroy() })
      req.pipe(proxy)
      return
    }
    if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405); return res.end() }
    let decoded
    try { decoded = decodeURIComponent(rawPath) } catch { res.writeHead(400); return res.end() }
    const filename = path.resolve(root, '.' + decoded)
    if (decoded.includes('\0') || !(filename === root || filename.startsWith(root + path.sep))) { res.writeHead(403); return res.end() }
    let asset = filename
    try { if (!fs.statSync(asset).isFile()) asset = path.join(root, 'index.html') } catch {
      // Missing JS/CSS/assets must fail; returning HTML breaks module loading.
      if (path.extname(decoded)) { res.writeHead(404); return res.end() }
      asset = path.join(root, 'index.html')
    }
    res.setHeader('content-type', types[path.extname(asset)] || 'application/octet-stream')
    res.setHeader('x-content-type-options', 'nosniff')
    res.setHeader('cache-control', 'no-cache')
    if (req.method === 'HEAD') return res.end()
    const stream = fs.createReadStream(asset)
    stream.on('error', () => { if (!res.headersSent) res.writeHead(404); res.end() })
    stream.pipe(res)
  })
  server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)) })
  server.on('upgrade', (req, socket, head) => {
    if (!allowed(req) || !req.url.split('?')[0].startsWith('/ws/')) { socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return }
    const proxy = transport.request({ hostname: target.hostname.replace(/^\[|\]$/g, ''), port: target.port || undefined, protocol: target.protocol, path: req.url, method: 'GET', headers: proxyHeaders(req) })
    upstreams.add(proxy)
    proxy.once('close', () => upstreams.delete(proxy))
    proxy.on('upgrade', (response, remote, upstreamHead) => {
      sockets.add(remote); remote.once('close', () => sockets.delete(remote))
      socket.write(`HTTP/1.1 ${response.statusCode} ${response.statusMessage}\r\n` + Object.entries(response.headers).map(([k, v]) => `${k}: ${v}\r\n`).join('') + '\r\n')
      if (upstreamHead.length) socket.write(upstreamHead)
      if (head.length) remote.write(head)
      remote.pipe(socket); socket.pipe(remote)
      remote.on('error', () => socket.destroy()); socket.on('error', () => remote.destroy())
      remote.on('close', () => socket.destroy()); socket.on('close', () => remote.destroy())
    })
    proxy.on('response', response => { response.resume(); socket.end(`HTTP/1.1 ${response.statusCode} Service Error\r\nConnection: close\r\n\r\n`) })
    proxy.on('error', () => socket.destroy())
    proxy.setTimeout(10000, () => proxy.destroy())
    socket.once('close', () => proxy.destroy())
    proxy.end()
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve) })
  origin = `http://127.0.0.1:${server.address().port}`
  return { origin, port: server.address().port, close: () => new Promise(resolve => { for (const request of upstreams) request.destroy(); for (const socket of sockets) socket.destroy(); server.close(resolve) }) }
}

module.exports = { normalizeServerURL, loadConnection, saveConnection, checkServer, startGateway }
