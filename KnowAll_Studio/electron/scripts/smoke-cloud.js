// Actual packaged desktop -> actual cloud entry -> actual private workers.
// Only learning content preparation is deterministic; no HTTP routes are mocked.
const { _electron } = require('../../frontend/node_modules/playwright')
const { spawn } = require('node:child_process')
const { createInterface } = require('node:readline')
const { once } = require('node:events')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

const root = path.resolve(__dirname, '..')
const backend = path.resolve(root, '..', 'backend')
const output = path.join(root, 'release', 'cloud-integration')

function startCloud() {
  const child = spawn(process.env.KNOWALL_TEST_PYTHON || 'python', ['scripts/cloud_integration_fixture.py'], {
    cwd: backend, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
    env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
  })
  let failure, tail = ''
  const seen = new Map(), waiting = new Map()
  const fail = error => {
    failure = error
    for (const waiter of waiting.values()) { clearTimeout(waiter.timer); waiter.reject(error) }
    waiting.clear()
  }
  child.stderr.on('data', chunk => { tail = (tail + chunk.toString()).slice(-12000) })
  const lines = createInterface({ input: child.stdout })
  lines.on('line', line => {
    if (!line.startsWith('KNOWALL_INTEGRATION ')) return
    try {
      const message = JSON.parse(line.slice('KNOWALL_INTEGRATION '.length))
      seen.set(message.event, message)
      const waiter = waiting.get(message.event)
      if (waiter) { clearTimeout(waiter.timer); waiting.delete(message.event); waiter.resolve(message) }
    } catch (error) { fail(error) }
  })
  child.on('error', fail)
  child.stdin.on('error', fail)
  child.on('exit', code => {
    if (code !== 0 || !seen.has('CLOSED')) fail(new Error(`Cloud fixture exited (${code}):\n${tail}`))
  })
  function event(name, timeout = 120000) {
    if (seen.has(name)) return Promise.resolve(seen.get(name))
    if (failure) return Promise.reject(failure)
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { waiting.delete(name); reject(new Error(`Cloud ${name} timeout:\n${tail}`)) }, timeout)
      waiting.set(name, { resolve, reject, timer })
    })
  }
  return { child, event, stderr: () => tail, command: text => {
    if (child.exitCode !== null || child.stdin.destroyed) return false
    return child.stdin.write(text + '\n')
  }, async close() {
    if (child.exitCode !== null) { lines.close(); return }
    const closed = once(child, 'exit')
    child.stdin.end('quit\n')
    let timer
    try {
      await Promise.race([closed, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Cloud cleanup timeout')), 30000) })])
      await event('CLOSED', 1000)
    } catch (error) {
      // Only the process created above and its children; never all Python apps.
      if (process.platform === 'win32' && Number.isInteger(child.pid) && child.pid > 0) {
        const kill = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
        await once(kill, 'exit')
      } else child.kill('SIGTERM')
      throw error
    } finally { clearTimeout(timer); lines.close() }
  } }
}

async function request(page, url, body, method) {
  return page.evaluate(async ({ url, body, method }) => {
    const response = await fetch(url, { method: method || (body === undefined ? 'GET' : 'POST'), headers: {
      Authorization: 'Bearer ' + localStorage.getItem('knowall_token'),
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    }, body: body === undefined ? undefined : JSON.stringify(body) })
    const text = await response.text()
    let data
    try { data = JSON.parse(text) } catch { data = text }
    return { status: response.status, data }
  }, { url, body, method })
}

async function launchClient(executablePath, data, origin, account) {
  const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.KNOWALL_SERVER_URL
  const client = await _electron.launch({ executablePath, env, timeout: 30000 })
  try {
    const connection = await client.firstWindow()
    await connection.locator('#server').fill(origin)
    const opened = client.waitForEvent('window')
    await connection.locator('#submit').click()
    const page = await opened
    await page.getByLabel('账号', { exact: true }).waitFor({ timeout: 30000 })
    await page.getByLabel('账号', { exact: true }).fill(account.username)
    await page.getByLabel('密码', { exact: true }).fill(account.password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.locator('.product-shell').waitFor({ timeout: 30000 })
    return { client, page }
  } catch (error) { await client.close(); throw error }
}

async function selectDocument(page) {
  const origin = new URL(page.url()).origin
  await page.goto(origin + '/learning')
  await page.getByRole('combobox', { name: '学习文档' }).click()
  await page.getByText('integration-biology.md', { exact: true }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '章节练习', exact: true }).waitFor({ timeout: 30000 })
}

async function match(page, secondAnswer) {
  for (const [label, answer] of [['A', '遗传物质'], ['B', secondAnswer]]) {
    const control = page.getByRole('combobox', { name: label + ' 的答案' })
    await control.click()
    // Use the focused combobox, as a keyboard user does. Virtual aria nodes
    // are hidden, and the previous popup can still exist during its animation.
    if (answer === '能量供应') await control.press('ArrowDown')
    await control.press('Enter')
  }
  await page.getByRole('button', { name: '提交答案', exact: true }).click()
}

async function downloadJSON(client, page, destination) {
  // Observe Electron's native download and supply the test-only save path.
  // A browser download event alone does not prove an Electron file was saved.
  await client.evaluate(({ session }, destination) => {
    globalThis.__knowallIntegrationDownload = null
    session.defaultSession.once('will-download', (_event, item) => {
      item.setSavePath(destination)
      item.once('done', (_event, state) => {
        globalThis.__knowallIntegrationDownload = { state, filename: item.getFilename() }
      })
    })
  }, destination)
  await page.getByRole('button', { name: '下载 JSON', exact: true }).click()
  const saved = await client.evaluate(async () => {
    const deadline = Date.now() + 20000
    while (!globalThis.__knowallIntegrationDownload && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    if (!globalThis.__knowallIntegrationDownload) throw new Error('Native Electron download did not complete')
    return globalThis.__knowallIntegrationDownload
  })
  assert.equal(saved.state, 'completed')
  return JSON.parse(await fs.readFile(destination, 'utf8'))
}

async function smoke() {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-real-cloud-desktop-'))
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release', 'win-unpacked', 'KnowAll Studio.exe')
  const clients = []
  const cloud = startCloud()
  let page, report
  await fs.mkdir(output, { recursive: true })
  await fs.rm(path.join(output, 'result.json'), { force: true })
  try {
    const ready = await cloud.event('READY')
    console.log('Cloud ready: central account service + two isolated real workers; deterministic test content only')
    const first = await launchClient(executablePath, path.join(data, 'first'), ready.origin, ready.accounts[0])
    clients.push(first.client); page = first.page
    await selectDocument(page)
    const base = '/api/v1/learning/packs/' + ready.pack_id
    await page.getByRole('button', { name: '章节练习', exact: true }).click()
    await match(page, '遗传物质')
    await page.getByText('得分：50%', { exact: true }).waitFor()
    await page.getByRole('button', { name: '定位来源原文', exact: true }).click()
    await page.locator('.ant-modal').getByText(/细胞核保存遗传物质；线粒体/).waitFor()
    await page.getByRole('button', { name: 'Close', exact: true }).click()
    await page.screenshot({ path: path.join(output, 'partial-answer.png') })
    console.log('Actual client: structured answer scored 50%, source document opened')

    // A separate installation/userData directory acts as a second device.
    const second = await launchClient(executablePath, path.join(data, 'second'), ready.origin, ready.accounts[0])
    clients.push(second.client)
    const copied = await request(second.page, base + '/session', { mode: 'smart' })
    assert.equal(copied.status, 200)
    assert.deepEqual(copied.data.items.map(x => x.reason), ['wrong_answer', 'due'])
    assert.equal(copied.data.items[0].question.id, ready.question_id)
    assert.equal((await request(second.page, '/api/v1/documents/')).data[0].id, ready.document_id)
    assert.notEqual(new URL(page.url()).origin, new URL(second.page.url()).origin, 'clients have independent local gateways')
    const progress = { answers: { '0': 1 }, idx: 1, agreed: true }
    assert.equal((await request(page, '/api/v1/quiz/progress', progress, 'PUT')).status, 200)
    assert.deepEqual((await request(second.page, '/api/v1/quiz/progress')).data.data.answers, progress.answers)
    console.log('Second actual client: same learning space, wrong answers, cards and legacy quiz progress')

    await page.getByRole('button', { name: '下一项', exact: true }).click()
    await page.getByRole('button', { name: '智能复习', exact: true }).click()
    await page.getByText('错题回访', { exact: true }).waitFor()
    await match(page, '能量供应')
    await page.getByText('得分：100%', { exact: true }).waitFor()
    await page.getByRole('button', { name: '下一项', exact: true }).click()
    await page.getByRole('button', { name: '显示答案', exact: true }).click()
    await page.getByText('参与细胞呼吸，为细胞活动提供能量。', { exact: true }).waitFor()
    await page.getByRole('button', { name: '简单', exact: true }).click()
    await page.getByText('本轮练习已完成', { exact: true }).waitFor()
    await page.screenshot({ path: path.join(output, 'review-complete.png') })
    assert.equal((await downloadJSON(first.client, page, path.join(data, 'learning-pack.json'))).id, ready.pack_id)
    assert.equal((await request(second.page, base + '/session', { mode: 'smart' })).data.items.length, 0)

    // Recycle real worker processes, keeping the central gateway and data.
    cloud.command('recycle')
    await cloud.event('RECYCLED', 20000)
    console.log('Private workers stopped; checking persisted state through a fresh worker')
    const restored = await request(second.page, base + '/session', { mode: 'smart' })
    assert.equal(restored.status, 200, JSON.stringify(restored))
    assert.deepEqual(restored.data.items, [])
    assert.equal((await request(second.page, '/api/v1/documents/')).data[0].id, ready.document_id)
    assert.equal((await request(second.page, '/api/v1/flashcards/due?deck_id=' + ready.deck_id)).data.due_count, 0)
    assert.deepEqual((await request(second.page, '/api/v1/quiz/progress')).data.data.answers, progress.answers)

    // Login to another account over the same real gateway; no client API stubs.
    const denial = await second.page.evaluate(async ({ account, document, base }) => {
      const login = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(account) })
      const auth = await login.json()
      const headers = { Authorization: 'Bearer ' + auth.access_token }
      const docs = await fetch('/api/v1/documents/', { headers })
      const detail = await fetch('/api/v1/documents/' + document, { headers })
      const pack = await fetch(base + '/export', { headers })
      return { login: login.status, documents: await docs.json(), detail: detail.status, pack: pack.status }
    }, { account: ready.accounts[1], document: ready.document_id, base })
    assert.deepEqual(denial, { login: 200, documents: [], detail: 404, pack: 404 })
    cloud.command('verify')
    const persistence = await cloud.event('PERSISTENCE', 10000)
    assert.equal(persistence.answers, 2, 'one wrong and one correct submission, with no duplicate retries')
    assert.equal(persistence.correct_answers, 1)
    assert.equal(persistence.review_count, 1)
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    report = { passed: true, checked_at: new Date().toISOString(),
      actual_executable: executablePath, app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))),
      bundled_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend', 'index.html'))),
      real_cloud_workers: true, deterministic_content_only: true, cross_client_sync: true,
      worker_restart_persistence: true, other_account_denied: true, json_download: true, legacy_quiz_progress: true, persistence }
  } catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {})
    const requested = cloud.command('diagnostics')
    const diagnostics = requested ? await cloud.event('DIAGNOSTICS', 3000).catch(() => null) : null
    await fs.writeFile(path.join(output, 'failure-diagnostics.json'), JSON.stringify({ error: String(error), diagnostics, server_stderr: cloud.stderr() }, null, 2))
    throw error
  } finally {
    // Verify these are our generated paths before removing anything recursively.
    assert.equal(path.dirname(data), path.resolve(os.tmpdir()))
    assert.ok(path.basename(data).startsWith('knowall-real-cloud-desktop-'))
    const cleanup = await Promise.allSettled(clients.reverse().map(client => client.close()))
    try { await cloud.close() } finally { await fs.rm(data, { recursive: true, force: true }) }
    const failures = cleanup.filter(result => result.status === 'rejected')
    if (failures.length) throw new AggregateError(failures.map(result => result.reason), 'Client cleanup failed')
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log('PASS actual cloud integration: login, real upload, partial score, source, wrong-answer revisit, FSRS review, JSON download, two clients, quiz progress, worker restart, cross-account isolation, cleanup')
}
smoke().catch(error => { console.error(error); process.exitCode = 1 })
