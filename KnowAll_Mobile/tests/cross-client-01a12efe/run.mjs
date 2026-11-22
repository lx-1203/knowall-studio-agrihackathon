// Execute the actual client API modules in separate browser contexts against real JWT/SQL/TCP.
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs/promises'
import { createServer } from 'node:net'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { randomBytes, createHash } from 'node:crypto'
import assert from 'node:assert/strict'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const mobileRequire = createRequire(path.join(root, 'KnowAll_Mobile/package.json'))
const desktopRequire = createRequire(path.join(root, 'KnowAll_Studio/frontend/package.json'))
const { build } = desktopRequire('esbuild')
const { chromium } = desktopRequire('@playwright/test')
const runName = process.env.CROSS_EVIDENCE_RUN || 'run02'
if (!/^[a-zA-Z0-9_-]+$/.test(runName)) throw Error('Invalid evidence run name')
const evidence = path.join(root, 'KnowAll_Mobile/docs/patrol/cross-01a12efe-round12', runName)
const assets = path.join(evidence, 'generated')
await fs.mkdir(path.dirname(evidence), { recursive: true })
// Reserve the run directory before any outputs: a rerun must never overwrite evidence.
await fs.mkdir(evidence)
await fs.mkdir(assets)
const probe = createServer(); probe.listen(0, '127.0.0.1'); await once(probe, 'listening')
const port = probe.address().port; await new Promise(resolve => probe.close(resolve))
const origin = `http://127.0.0.1:${port}`
const inputs = new Map()
const backendInputs = []
for (const relative of ['app/api/auth.py', 'app/api/documents.py', 'app/api/learning.py', 'app/api/flashcards.py',
  'app/core/auth.py', 'app/core/documents.py', 'app/core/study_pack_service.py', 'app/core/memory/__init__.py',
  'app/core/learning_workbench.py',
  'app/database.py', 'app/models/study_pack.py']) {
  const file = path.join(root, 'KnowAll_Studio/backend', relative)
  backendInputs.push({ path: path.relative(root, file).replaceAll('\\', '/'), sha256: createHash('sha256').update(await fs.readFile(file)).digest('hex') })
}
await fs.writeFile(path.join(evidence, 'backend-inputs-before.json'), JSON.stringify(backendInputs, null, 2))
for (const kind of ['mobile', 'desktop']) {
  const input = path.join(root, kind === 'mobile' ? 'KnowAll_Mobile/src/api.ts' : 'KnowAll_Studio/frontend/src/api.ts')
  const built = await build({ entryPoints: [input], outfile: path.join(assets, `${kind}.js`),
    bundle: true, format: 'esm', platform: 'browser', metafile: true,
    alias: kind === 'mobile' ? { 'react-native': mobileRequire.resolve('react-native-web') } : {},
    define: { 'process.env.EXPO_PUBLIC_API_URL': JSON.stringify(origin + '/api/v1'),
      'process.env.EXPO_PUBLIC_WS_URL': JSON.stringify(origin), 'process.env.NODE_ENV': '"production"' },
    logLevel: 'warning' })
  await fs.writeFile(path.join(evidence, `${kind}-metafile.json`), JSON.stringify(built.metafile, null, 2))
  for (const inputPath of Object.keys(built.metafile.inputs)) {
    const absolute = path.resolve(inputPath)
    const bytes = await fs.readFile(absolute)
    inputs.set(absolute, { path: path.relative(root, absolute).replaceAll('\\', '/'),
      sha256: createHash('sha256').update(bytes).digest('hex') })
  }
}
await fs.writeFile(path.join(evidence, 'inputs-before.json'), JSON.stringify([...inputs.values()], null, 2))

let server, browser
const jwtSecret = randomBytes(32).toString('hex')
const traces = [], scenes = [], errors = []
const start = async () => {
  server = spawn('python', [fileURLToPath(new URL('./server.py', import.meta.url)),
    '--db', path.join(evidence, 'isolated.sqlite'), '--assets', assets, '--port', String(port)],
    { cwd: root, windowsHide: true, env: { ...process.env, CROSS_TEST_JWT: jwtSecret } })
  let output = ''
  server.stdout.on('data', data => { output += data })
  server.stderr.on('data', data => { output += data })
  for (let i = 0; i < 200; i++) {
    if (server.exitCode !== null) throw Error(`Fixture exited ${server.exitCode}: ${output}`)
    try { const res = await fetch(origin + '/__test/health'); if (res.ok) return (await res.json()).pid } catch {}
    await new Promise(resolve => setTimeout(resolve, 150))
  }
  throw Error('Fixture readiness timeout: ' + output)
}
const stop = async () => {
  if (!server || server.exitCode !== null) return
  const exited = once(server, 'exit'); server.kill(); await exited
}
const open = async (context, kind) => {
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(origin + '/__test/page/' + kind)
  await page.waitForFunction(() => !!window.client)
  return page
}
const call = (page, method, ...args) => page.evaluate(async ({ method, args }) => {
  try { return { value: await window.client[method](...args) } }
  catch (error) { return { status: error.response?.status ?? 0, error: error.message } }
}, { method, args })
const ok = async (page, method, ...args) => {
  const result = await call(page, method, ...args)
  assert.equal(result.status, undefined, `${method} failed with status ${result.status}`)
  return result.value
}
const login = async (page, username = 'cross_a') => {
  const result = await ok(page, 'login', { username, password: 'LocalTestOnly_2026!' })
  await ok(page, 'setAuthToken', result.access_token)
  return result.user.id
}
const state = page => page.evaluate(async () => (await fetch('/__test/state', {
  headers: { Authorization: 'Bearer ' + localStorage.getItem('knowall_token') } })).json())
const trace = page => page.evaluate(async () => (await fetch('/__test/trace', {
  headers: { Authorization: 'Bearer ' + localStorage.getItem('knowall_token') } })).json())
const scene = (name, values = {}) => { scenes.push({ name, passed: true, ...values }); console.log('PASS: ' + name) }

try {
  const firstPid = await start()
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const phoneContext = await browser.newContext(), desktopContext = await browser.newContext()
  const phone = await open(phoneContext, 'mobile'), desktop = await open(desktopContext, 'desktop')
  assert.equal(await login(phone), 'cross-a'); assert.equal(await login(desktop), 'cross-a')
  scene('real password login on both API clients, separate browser contexts')
  const listPhone = await ok(phone, 'listDocuments'), listDesktop = await ok(desktop, 'listDocuments')
  assert.deepEqual(listPhone, listDesktop)
  scene('same-account document list matches')
  const doc = await ok(phone, 'getDocumentDetail', 'knowledge-a')
  assert.equal(doc.material_kind, 'knowledge')
  assert.equal((await ok(desktop, 'getDocumentDetail', 'question_bank-a')).material_kind, 'question_bank')
  assert.equal(await ok(phone, 'getMaterialLearningPack', 'question_bank-a'), null)
  assert.deepEqual(await ok(phone, 'getDocumentChunks', 'knowledge-a'), await ok(desktop, 'getDocumentChunks', 'knowledge-a'))
  scene('source text matches; independent question-bank document has no generated pack')
  await ok(phone, 'updateDocumentOrganization', 'knowledge-a', { subject: '手机分类', material_kind: 'mixed' })
  assert.equal((await ok(desktop, 'getDocumentDetail', 'knowledge-a')).subject, '手机分类')
  await ok(desktop, 'updateDocumentOrganization', 'knowledge-a', { subject: '电脑分类', material_kind: 'knowledge' })
  assert.equal((await ok(phone, 'getDocumentDetail', 'knowledge-a')).subject, '电脑分类')
  scene('organization writes read back in both directions')
  let pack = await ok(phone, 'getMaterialLearningPack', 'knowledge-a')
  const rawPack = await ok(desktop, 'getLearningPack', 'knowledge-a')
  assert.equal(pack.id, rawPack.id)
  const practice = await ok(phone, 'startMaterialPractice', pack, 'practice')
  assert.deepEqual(practice, await ok(desktop, 'startLearningSession', pack.id, 'practice', 20))
  const qid = practice.items[0].question.id, cards = pack.sections.flatMap(s => s.flashcards)
  assert.equal((await ok(phone, 'submitMaterialAnswer', pack.id, qid, 'B', 250)).is_correct, false)
  assert((await ok(desktop, 'startLearningSession', pack.id, 'smart', 20)).items.some(i => i.question?.id === qid && i.reason === 'wrong_answer'))
  assert.equal((await ok(desktop, 'submitLearningAnswer', pack.id, qid, 'A', 300)).is_correct, true)
  assert(!(await ok(phone, 'startMaterialPractice', pack, 'smart')).items.some(i => i.question?.id === qid))
  scene('phone wrong answer visible on desktop; desktop correction updates phone smart session')
  const cardIds = value => (Array.isArray(value) ? value : value.cards).map(c => c.id ?? c.card_id)
  assert(cardIds(await ok(desktop, 'getDueCards', 100)).includes(cards[0].id))
  await ok(phone, 'reviewMaterialCard', pack, cards[0].id, 4)
  assert(!cardIds(await ok(desktop, 'getDueCards', 100)).includes(cards[0].id))
  await ok(desktop, 'reviewLearningCard', pack.id, cards[1].id, 4)
  assert(!(await ok(phone, 'startMaterialPractice', pack, 'smart')).items.some(i => cards.some(c => c.id === i.card?.id)))
  let before = await state(phone)
  assert.equal(before.answers.length, 2); assert.equal(before.reviews.length, 2)
  assert(before.schedules.every(s => s.count === 1))
  scene('card ratings and due schedules shared in both directions with SQL counts', { state: before })
  // Lose an HTTP response only after the real backend has committed, then verify no replay.
  const lostAnswer = async route => { assert.equal((await route.fetch()).status(), 200); await route.abort('failed') }
  await phone.route('**/learning/packs/*/answers', lostAnswer)
  assert.equal((await call(phone, 'submitMaterialAnswer', pack.id, qid, 'A', 200)).status, 0)
  await phone.unroute('**/learning/packs/*/answers', lostAnswer)
  const lostRating = async route => { assert.equal((await route.fetch()).status(), 200); await route.abort('failed') }
  await desktop.route('**/learning/packs/*/review', lostRating)
  assert.equal((await call(desktop, 'reviewLearningCard', pack.id, cards[0].id, 4)).status, 0)
  await desktop.unroute('**/learning/packs/*/review', lostRating)
  before = await state(desktop)
  assert.equal(before.answers.length, 3); assert.equal(before.reviews.length, 3)
  assert.equal(before.schedules.find(s => s.card_id === cards[0].id).count, 2)
  assert(!(await ok(desktop, 'startLearningSession', pack.id, 'smart', 20)).items.some(i => i.question?.id === qid))
  assert(!(await ok(phone, 'startMaterialPractice', pack, 'smart')).items.some(i => i.card?.id === cards[0].id))
  scene('lost committed answer/rating responses are not automatically replayed; other client reads committed state', { state: before })
  await phone.reload(); await phone.waitForFunction(() => !!window.client); await ok(phone, 'restoreToken')
  await desktop.reload(); await desktop.waitForFunction(() => !!window.client)
  assert.equal((await ok(phone, 'getMaterialLearningPack', 'knowledge-a')).id, pack.id)
  assert.equal((await ok(desktop, 'getDocumentDetail', 'knowledge-a')).subject, '电脑分类')
  scene('browser reload restores each client token and server-backed state')
  await login(phone, 'cross_b')
  for (const [method, args] of [['getDocumentDetail', ['knowledge-a']], ['getDocumentChunks', ['knowledge-a']],
    ['getMaterialLearningPack', ['knowledge-a']], ['updateDocumentOrganization', ['knowledge-a', { subject: '不能修改', material_kind: 'knowledge' }]],
    ['submitMaterialAnswer', [pack.id, qid, 'B', 1]], ['reviewMaterialCard', [pack, cards[0].id, 4]]]) {
    // Existing document-learning ownership contract uses 403 for /documents/{id}/pack;
    // specific owned-pack mutations and document routes conceal foreign resources with 404.
    assert.equal((await call(phone, method, ...args)).status, method === 'getMaterialLearningPack' ? 403 : 404, method)
  }
  assert.equal((await ok(phone, 'getDocumentDetail', 'knowledge-b')).id, 'knowledge-b')
  assert.equal((await state(phone)).answers.length, 0)
  scene('account B cannot read or modify account A source, pack, answers or ratings')
  await login(phone)
  traces.push(await trace(desktop)); await stop()
  const secondPid = await start(); assert.notEqual(firstPid, secondPid)
  assert.equal((await ok(phone, 'getDocumentDetail', 'knowledge-a')).subject, '电脑分类')
  const after = await state(desktop)
  assert.deepEqual(after.answers, before.answers); assert.deepEqual(after.schedules, before.schedules)
  assert.deepEqual(after.reviews, before.reviews)
  assert.equal((await ok(desktop, 'getLearningPack', 'knowledge-a')).id, pack.id)
  scene('backend restart with a new PID preserves organization, pack, history and schedules', { firstPid, secondPid })
  await phone.evaluate(async () => { await fetch('/__test/source-change', { method: 'POST',
    headers: { Authorization: 'Bearer ' + localStorage.getItem('knowall_token') } }) })
  pack = await ok(phone, 'getMaterialLearningPack', 'knowledge-a'); assert.equal(pack.stale, true)
  assert.equal((await ok(desktop, 'getLearningPack', 'knowledge-a')).stale, true)
  assert.equal((await call(desktop, 'submitLearningAnswer', pack.id, qid, 'A', 1)).status, 409)
  // Mobile normalized stale packs are blocked before HTTP; raw answer API still enforces server freshness.
  assert.equal((await call(phone, 'submitMaterialAnswer', pack.id, qid, 'A', 1)).status, 409)
  scene('source version change is visible to both clients; stale writes rejected')
  await ok(phone, 'setAuthToken', 'invalid-test-token')
  assert.equal((await call(phone, 'getMaterialLearningPack', 'knowledge-a')).status, 401)
  await desktop.evaluate(() => window.client.setAuthToken('invalid-test-token'))
  assert.equal((await call(desktop, 'getLearningPack', 'knowledge-a')).status, 401)
  scene('invalid JWT rejected on both authenticated learning clients')
  await login(desktop)
  traces.push(await trace(desktop))
  assert.equal((await state(desktop)).ai_calls, 0); assert.equal(errors.length, 0)
  for (const traceBatch of traces) assert.equal(traceBatch.ai_calls, 0)
  await fs.writeFile(path.join(evidence, 'trace.json'), JSON.stringify(traces, null, 2))
  await fs.writeFile(path.join(evidence, 'results.json'), JSON.stringify({ scenes, errors, ai_calls: 0 }, null, 2))
  const drift = []
  for (const [file, row] of inputs) {
    if (createHash('sha256').update(await fs.readFile(file)).digest('hex') !== row.sha256) drift.push(row.path)
  }
  for (const row of backendInputs) {
    if (createHash('sha256').update(await fs.readFile(path.join(root, row.path))).digest('hex') !== row.sha256) drift.push(row.path)
  }
  await fs.writeFile(path.join(evidence, 'input-check.json'), JSON.stringify({ client_inputs: inputs.size, backend_inputs: backendInputs.length, drift }, null, 2))
  assert.deepEqual(drift, [])
  console.log(`PASS: ${scenes.length} cross-client scenarios; real auth/TCP/SQLite, no AI, no product UI/native claim.`)
} finally { try { if (browser) await browser.close() } finally { await stop() } }
