// Actual packaged desktop -> actual cloud entry -> actual private workers.
// Only learning content preparation is deterministic; no HTTP routes are mocked.
const { _electron } = require('../../frontend/node_modules/playwright')
const { spawn, spawnSync } = require('node:child_process')
const { createInterface } = require('node:readline')
const { once } = require('node:events')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

const root = path.resolve(__dirname, '..')
const backend = process.env.KNOWALL_TEST_BACKEND || path.resolve(root, '..', 'backend')
const output = process.env.KNOWALL_EXPORT_SMOKE_OUTPUT || path.join(root, 'release', 'flashcard-export-round19')

function startCloud() {
  const args = ['scripts/cloud_integration_fixture.py']
  if (process.env.KNOWALL_VERIFY_MARKDOWN === '1') args.push('--markdown-flashcard')
  const child = spawn(process.env.KNOWALL_TEST_PYTHON || 'python', args, {
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

async function smoke() {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-flashcard-export-'))
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release', 'oss-round18', 'win-unpacked', 'KnowAll Studio.exe')
  const cloud = startCloud()
  let client, page, report, failure
  await fs.mkdir(output, { recursive: true })
  try {
    const ready = await cloud.event('READY')
    console.log('Actual temporary cloud accounts/workers ready; deterministic source content only')
    const opened = await launchClient(executablePath, data, ready.origin, ready.accounts[0])
    client = opened.client; page = opened.page
    const decks = await request(page, '/api/v1/flashcards/decks')
    assert.equal(decks.status, 200)
    const deck = decks.data.find(d => d.id === ready.deck_id)
    assert.ok(deck && /[^\x00-\x7f]/.test(deck.name), 'Use the real private Chinese deck')
    await page.goto(new URL('/flashcards', page.url()).href)
    const row = page.getByRole('row').filter({ hasText: deck.name })
    await row.waitFor({ timeout: 30000 })
    let richUI = false, persistedReview = false, sessionCompleted = false, restartedDueEmpty = false
    if (process.env.KNOWALL_VERIFY_MARKDOWN === '1') {
      const face = page.locator('.flashcard-flip')
      await face.waitFor({ timeout: 30000 })
      assert.equal(await face.locator('table').count(), 0, 'Answer must be hidden initially')
      await face.click()
      await face.locator('table').waitFor({ timeout: 30000 })
      assert.deepEqual(await face.locator('table td').allTextContents(), ['线粒体', '供能', '细胞核', '遗传物质'])
      assert.equal(await face.locator('pre code').textContent().then(text => text.replace(/\n$/, '')), 'if energy < 2:\n    print("供能")')
      assert.deepEqual(await face.locator('ul li').allTextContents(), ['细胞呼吸', '能量供应'])
      assert.equal(await face.locator('img,script,iframe,a[href^="javascript:"]').count(), 0)
      await face.locator('pre').click()
      assert.equal(await face.locator('table').count(), 1, 'Code click must keep answer open')
      const link = face.getByRole('link', { name: '安全资料' })
      assert.equal(await link.getAttribute('target'), '_blank')
      assert.equal(await link.getAttribute('rel'), 'noopener noreferrer')
      // Exercise the real desktop window-open handler, stub only the OS browser
      // launch so no third-party website opens during this local verification.
      await client.evaluate(({ shell }) => {
        globalThis.__originalExternal = shell.openExternal
        globalThis.__richExternal = null
        shell.openExternal = url => { globalThis.__richExternal = url; return Promise.resolve() }
      })
      try {
        await link.click()
        const external = await client.evaluate(async () => {
          const deadline = Date.now() + 10000
          while (!globalThis.__richExternal && Date.now() < deadline) await new Promise(r => setTimeout(r, 50))
          return globalThis.__richExternal
        })
        assert.equal(external, 'https://example.test/biology')
        assert.equal(await face.locator('table').count(), 1)
      } finally {
        await client.evaluate(({ shell }) => { shell.openExternal = globalThis.__originalExternal; delete globalThis.__originalExternal; delete globalThis.__richExternal })
      }
      await page.screenshot({ path: path.join(output, 'private-rich-card.png') })
      const reviewResponse = page.waitForResponse(r => r.url().endsWith('/flashcards/review') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '正常回忆 (3)' }).click()
      const response = await reviewResponse
      assert.equal(response.status(), 200)
      assert.deepEqual(response.request().postDataJSON(), { card_id: ready.card_id, rating: 3 })
      assert.equal((await response.json()).review_count, 1)
      await page.getByText('本轮复习完成', { exact: true }).waitFor({ timeout: 30000 })
      assert.equal(await page.locator('.flashcard-flip').count(), 0)
      assert.equal(await page.getByRole('button', { name: '正常回忆 (3)', exact: true }).count(), 0)
      await page.screenshot({ path: path.join(output, 'private-session-completed.png') })
      sessionCompleted = true
      // Close private workers, then independently read the persisted SQLite state.
      cloud.command('recycle'); await cloud.event('RECYCLED')
      cloud.command('verify-flashcard')
      const savedReview = await cloud.event('FLASHCARD_PERSISTENCE')
      assert.equal(savedReview.review_count, 1)
      const due = await request(page, '/api/v1/flashcards/due?limit=20&deck_id=' + encodeURIComponent(deck.id))
      assert.equal(due.status, 200)
      assert.equal(due.data.due_count, 0)
      assert.deepEqual(due.data.cards, [])
      restartedDueEmpty = true
      richUI = persistedReview = true
    }
    const destination = path.join(data, 'download.apkg')
    await client.evaluate(({ session }, destination) => {
      globalThis.__flashcardExportDownload = null
      session.defaultSession.once('will-download', (_event, item) => {
        item.setSavePath(destination)
        item.once('done', (_event, state) => { globalThis.__flashcardExportDownload = { state, filename: item.getFilename() } })
      })
    }, destination)
    const responsePromise = page.waitForResponse(r => r.url().endsWith('/flashcards/export/anki/' + deck.id))
    await row.getByRole('button', { name: /导出Anki$/ }).click()
    const response = await responsePromise
    assert.equal(response.status(), 200)
    const token = await page.evaluate(() => localStorage.getItem('knowall_token'))
    assert.equal(response.request().headers().authorization, 'Bearer ' + token)
    const original = await response.body()
    const saved = await client.evaluate(async () => {
      const deadline = Date.now() + 20000
      while (!globalThis.__flashcardExportDownload && Date.now() < deadline) await new Promise(r => setTimeout(r, 100))
      if (!globalThis.__flashcardExportDownload) throw new Error('Native APKG download timeout')
      return globalThis.__flashcardExportDownload
    })
    assert.equal(saved.state, 'completed')
    assert.equal(saved.filename, deck.name.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_') + '.apkg')
    const downloaded = await fs.readFile(destination)
    assert.deepEqual(downloaded, original, 'Native saved bytes must equal the actual authenticated HTTP response')
    const verified = spawnSync(process.env.KNOWALL_TEST_PYTHON || 'python', ['-c', `
import sys,zipfile,sqlite3,json,pathlib,hashlib,os
p=pathlib.Path(sys.argv[1]);dbfile=p.with_suffix('.anki2')
with zipfile.ZipFile(p) as z:
 assert set(z.namelist())=={'collection.anki2','media'}
 assert json.loads(z.read('media'))=={}
 dbfile.write_bytes(z.read('collection.anki2'))
with sqlite3.connect(dbfile) as db:
 notes=db.execute('select flds from notes').fetchall();assert len(notes)==1 and '线粒体' in notes[0][0] and '供能' in notes[0][0]
 assert db.execute('select count(*) from cards').fetchone()[0]==1
 expected=sys.argv[2]+' ['+hashlib.sha256(('knowall-deck-v1:'+sys.argv[3]).encode()).digest()[:6].hex()+']'
 assert any(deck['name']==expected for deck in json.loads(db.execute('select decks from col').fetchone()[0]).values())
consumer_verified=False;markdown_verified=False
if os.environ.get('KNOWALL_VERIFY_ANKI')=='1':
 from anki.collection import Collection,ImportAnkiPackageRequest
 import genanki
 col=Collection(str(p.with_name('native-consumer.anki2')))
 try:
  for _ in range(2):col.import_anki_package(ImportAnkiPackageRequest(package_path=str(p)))
  assert col.note_count()==col.card_count()==1
  note=col.get_note(col.find_notes('')[0])
  assert note.guid==genanki.guid_for('knowall-note-v1',sys.argv[3],sys.argv[4])
  assert '线粒体' in note.fields[0] and '供能' in note.fields[1]
  if os.environ.get('KNOWALL_VERIFY_MARKDOWN')=='1':
   from lxml.html import fromstring
   answer=col.get_card(col.find_cards('')[0]).answer();dom=fromstring(answer)
   assert len(dom.xpath('//table'))==1
   assert [x.text_content() for x in dom.xpath('//table//td')]==['线粒体','供能','细胞核','遗传物质']
   assert dom.xpath('//pre/code')[0].text_content()=='if energy < 2:\\n    print("供能")\\n'
   assert len(dom.xpath('//ul/li'))==2 and dom.xpath('//strong')[0].text_content()=='供能要点'
   assert not dom.xpath('//img|//script|//iframe')
   assert all(not x.get('href','').lower().startswith(('javascript:','vbscript:','file:','data:')) for x in dom.xpath('//a'))
   p.with_name('native-answer.html').write_text('<!doctype html><meta charset="utf-8">'+answer,encoding='utf-8')
   markdown_verified=True
  consumer_verified=True
 finally:col.close()
print(json.dumps({'valid_zip':True,'notes':len(notes),'cards':1,'chinese_content':True,'actual_anki_consumer':consumer_verified,'actual_anki_markdown':markdown_verified}))
`, destination, deck.name, deck.id, ready.card_id], { encoding: 'utf8', windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } })
    assert.equal(verified.status, 0, verified.stderr)
    const content = JSON.parse(verified.stdout)
    await fs.copyFile(destination, path.join(output, 'native-private.apkg'))
    if (content.actual_anki_markdown) await fs.copyFile(path.join(data, 'native-answer.html'), path.join(output, 'native-answer.html'))
    const denial = await page.evaluate(async ({ account, id }) => {
      const login = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(account) })
      const auth = await login.json()
      const response = await fetch('/api/v1/flashcards/export/anki/' + id, { headers: { Authorization: 'Bearer ' + auth.access_token } })
      return { login: login.status, status: response.status }
    }, { account: ready.accounts[1], id: deck.id })
    assert.deepEqual(denial, { login: 200, status: 404 })
    await page.screenshot({ path: path.join(output, 'private-apkg-downloaded.png') })
    const resources = path.join(path.dirname(executablePath), 'resources')
    const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
    report = { passed: true, checked_at: new Date().toISOString(), actual_executable: executablePath,
      actual_backend: backend, backend_export_source_sha256: sha(await fs.readFile(path.join(backend, 'app/core/anki_export.py'))),
      app_asar_sha256: sha(await fs.readFile(path.join(resources, 'app.asar'))), bundled_index_sha256: sha(await fs.readFile(path.join(resources, 'frontend/index.html'))),
      real_cloud_workers: true, deterministic_content_only: true, actual_rich_ui: richUI, actual_review_persisted: persistedReview,
      actual_session_completed: sessionCompleted, actual_restarted_due_empty: restartedDueEmpty,
      external_link_os_launch_stubbed: richUI, bearer_verified: true, native_download: saved,
      bytes: downloaded.length, downloaded_sha256: sha(downloaded), response_sha256: sha(original), content, other_account: denial }
  } catch (error) {
    failure = error
    if (page) await page.screenshot({ path: path.join(output, 'failed.png') }).catch(() => {})
  } finally {
    try { if (client) await client.close() } catch (error) { failure ||= error }
    try { await cloud.close() } catch (error) { failure ||= error }
    // data is mkdtemp-created above; verify its parent and prefix before deleting.
    assert.equal(path.dirname(data), os.tmpdir())
    assert.ok(path.basename(data).startsWith('knowall-flashcard-export-'))
    await fs.rm(data, { recursive: true, force: true })
  }
  if (failure) throw failure
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log('PASS current EXE actual private cloud: Bearer, Chinese APKG, native saved response bytes, real ZIP/SQLite/cards, cross-account404 and cleanup')
}
module.exports = { startCloud, request, launchClient }
if (require.main === module) smoke().catch(error => { console.error(error); process.exitCode = 1 })

