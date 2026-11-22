// Actual unchanged desktop UI -> current extracted cloud -> real Anki updates.
const { startCloud, request, launchClient } = require('./smoke-flashcard-export')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const assert = require('node:assert/strict')

async function smoke() {
  const root = path.resolve(__dirname, '..')
  const output = process.env.KNOWALL_EXPORT_SMOKE_OUTPUT || path.join(root, 'release', 'anki-version-round26')
  await fs.mkdir(output, { recursive: true })
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-anki-version-'))
  const cloud = startCloud()
  let client, page, report
  const consumer = (mode, packages) => {
    const run = spawnSync(process.env.KNOWALL_TEST_PYTHON, ['-c', `
import sys,json,zipfile,sqlite3
from pathlib import Path
from anki.collection import Collection,ImportAnkiPackageRequest
c=Collection(sys.argv[1]);mode=sys.argv[2];paths=sys.argv[3:];mods=[]
try:
 for index,p in enumerate(paths):
  with zipfile.ZipFile(p) as z:
   db=Path(sys.argv[1]+'.check');db.write_bytes(z.read('collection.anki2'))
   with sqlite3.connect(db) as conn:mods.append(conn.execute('select mod from notes').fetchone()[0])
  c.import_anki_package(ImportAnkiPackageRequest(package_path=p))
  if mode=='first':
   card=c.get_card(c.find_cards('')[0]);card.queue=card.type=2;card.ivl=17;card.reps=4;card.due=35;c.update_card(card)
 assert c.note_count()==c.card_count()==1
 n=c.get_note(c.find_notes('')[0]);card=c.get_card(c.find_cards('')[0])
 assert (card.ivl,card.reps,card.due,card.queue)==(17,4,35,2)
 if mode=='updated':assert '线粒体' in n.fields[1]
 if mode=='pair':assert '同秒最终内容' in n.fields[1] and mods[1]>mods[0]
 print(json.dumps({'guid':n.guid,'note':n.id,'card':card.id,'mods':mods,'schedule':[card.ivl,card.reps,card.due,card.queue]}))
finally:c.close()
`, path.join(data, 'consumer.anki2'), mode, ...packages], {
      windowsHide: true, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' },
    })
    assert.equal(run.status, 0, run.stdout + run.stderr)
    return JSON.parse(run.stdout.trim().split('\n').at(-1))
  }
  try {
    const ready = await cloud.event('READY')
    ;({ client, page } = await launchClient(process.env.KNOWALL_DESKTOP_EXECUTABLE, path.join(data, 'desktop'), ready.origin, ready.accounts[1]))
    const deck = (await request(page, '/api/v1/flashcards/decks', { name: '原生内容版本牌组' })).data
    const card = (await request(page, '/api/v1/flashcards/cards', { deck_id: deck.id, card_type: 'qa', front: '细胞供能的结构是什么？', back: '旧答案细胞核', hints: '', tags: [] })).data
    const apiExport = async name => {
      const response = await page.request.get(new URL('/api/v1/flashcards/export/anki/' + deck.id, page.url()).href, {
        headers: { Authorization: 'Bearer ' + await page.evaluate(() => localStorage.getItem('knowall_token')) },
      })
      assert.equal(response.status(), 200, await response.text())
      const filename = path.join(output, name + '.apkg'); await fs.writeFile(filename, await response.body()); return filename
    }
    const firstPath = await apiExport('first'); const first = consumer('first', [firstPath])
    await page.getByText('统一复习', { exact: true }).click()
    await page.getByText(card.front, { exact: true }).waitFor()
    const due = (await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id)).data.cards
    await page.getByRole('button', { name: '编辑当前卡片', exact: true }).click()
    await page.getByLabel('答案 / 解释', { exact: true }).fill('**线粒体**供能')
    const saved = page.waitForResponse(r => r.url().endsWith('/flashcards/cards/' + card.id) && r.request().method() === 'PATCH')
    await page.getByRole('dialog').getByRole('button', { name: '保存修改', exact: true }).click()
    assert.equal((await saved).status(), 200); await page.getByRole('dialog').waitFor({ state: 'detached' })
    const after = (await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id)).data.cards
    assert.deepEqual(after.find(c => c.id === card.id).schedule, due.find(c => c.id === card.id).schedule)
    const updatedPath = await apiExport('updated'); const updated = consumer('updated', [updatedPath])
    assert.equal(updated.guid, first.guid); assert.equal(updated.card, first.card); assert.ok(updated.mods[0] > first.mods[0])
    cloud.command('recycle'); await cloud.event('RECYCLED')
    const restartedPath = await apiExport('restarted'); const restarted = consumer('updated', [restartedPath])
    assert.equal(restarted.mods[0], updated.mods[0]); assert.equal(restarted.card, first.card)
    // One in-page HTTP chain avoids test-driver round trips consuming the boundary.
    // All requests use the real gateway, credentials, SQL, encoder and wall clock.
    const attempts = []
    let pair, enteredSecond, endedSecond, exportElapsed
    for (let attempt = 1; attempt <= 3; attempt++) {
      const result = await page.evaluate(async ({ cardId, deckId, attempt }) => {
        const headers = { Authorization: 'Bearer ' + localStorage.getItem('knowall_token'), 'content-type': 'application/json' }
        const check = async response => { if (response.status !== 200) throw new Error('Actual HTTP ' + response.status + ': ' + await response.text()); return response }
        const read = await check(await fetch('/api/v1/flashcards/cards/' + cardId, { headers }))
        let current = await read.json()
        const edit = async back => {
          const response = await check(await fetch('/api/v1/flashcards/cards/' + cardId, { method: 'PATCH', headers, body: JSON.stringify({ ...current, back, original: current }) }))
          current = await response.json()
        }
        const exportBytes = async () => Array.from(new Uint8Array(await (await check(await fetch('/api/v1/flashcards/export/anki/' + deckId, { headers }))).arrayBuffer()))
        await edit('同秒第一内容线粒体 ' + attempt)
        // Prepare the first edit before aligning: PATCH latency must not consume
        // the second in which the actual first export claims its SQL timestamp.
        await new Promise(resolve => setTimeout(resolve, 1002 - Date.now() % 1000))
        const firstRequestAt = Date.now()
        const a = await exportBytes(), firstResponseAt = Date.now()
        await edit('同秒最终内容：**线粒体** ' + attempt)
        const started = Date.now(), enteredSecond = Math.floor(started / 1000)
        const b = await exportBytes(), ended = Date.now()
        return { a, b, firstRequestAt, firstResponseAt, enteredSecond, endedSecond: Math.floor(ended / 1000), elapsed_ms: ended - started }
      }, { cardId: card.id, deckId: deck.id, attempt })
      const pairA = path.join(output, 'same-second-a.apkg'), pairB = path.join(output, 'same-second-b.apkg')
      await fs.writeFile(pairA, Buffer.from(result.a)); await fs.writeFile(pairB, Buffer.from(result.b))
      pair = consumer('pair', [pairA, pairB])
      attempts.push({ attempt, first_request_ms: result.firstRequestAt, first_response_ms: result.firstResponseAt, enteredSecond: result.enteredSecond, first_mtime: pair.mods[0], next_mtime: pair.mods[1], response_end_second: result.endedSecond, elapsed_ms: result.elapsed_ms })
      await fs.writeFile(path.join(output, 'timing-attempts.json'), JSON.stringify(attempts, null, 2))
      assert.ok(pair.mods[1] <= result.endedSecond); assert.equal(pair.card, first.card); assert.equal(pair.guid, first.guid)
      if (result.enteredSecond === pair.mods[0]) {
        enteredSecond = result.enteredSecond; endedSecond = result.endedSecond; exportElapsed = result.elapsed_ms
        break
      }
      for (const [label, filename] of [['a', pairA], ['b', pairB]]) await fs.copyFile(filename, path.join(output, 'missed-boundary-' + attempt + '-' + label + '.apkg'))
    }
    assert.equal(enteredSecond, pair.mods[0], 'An actual second export must start in the first export second within three real attempts')
    await page.getByRole('button', { name: /刷新/ }).click()
    await page.screenshot({ path: path.join(output, 'current.png'), animations: 'disabled' })
    const denied = await page.evaluate(async ({ account, deck }) => {
      const r = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(account) }); const a = await r.json()
      const response = await fetch('/api/v1/flashcards/export/anki/' + deck, { headers: { Authorization: 'Bearer ' + a.access_token } })
      return response.status
    }, { account: ready.accounts[0], deck: deck.id })
    assert.equal(denied, 404)
    report = { at: new Date().toISOString(), passed: true, actual_executable: process.env.KNOWALL_DESKTOP_EXECUTABLE, actual_backend: process.env.KNOWALL_TEST_BACKEND,
      actual_native_edit: true, actual_default_anki_update: true, actual_schedule_identity_preserved: true, actual_restart_stable_mtime: true,
      real_same_second: { enteredSecond, first_mtime: pair.mods[0], next_mtime: pair.mods[1], response_end_second: endedSecond, elapsed_ms: exportElapsed, attempts },
      actual_foreign_account_404: true, no_http_mocks: true, no_fake_clock: true, no_paid_call: true, first, updated, restarted, pair }
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(output, 'failed.png'), animations: 'disabled' }).catch(() => {})
    throw error
  } finally {
    try { if (client) await client.close() } finally {
      await cloud.close()
      assert.equal(path.dirname(path.resolve(data)), path.resolve(os.tmpdir())); assert.ok(path.basename(data).startsWith('knowall-anki-version-'))
      await fs.rm(data, { recursive: true, force: true })
    }
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log('PASS actualDesktop/currentCloud/nativeEdit/realSameSecond/AnkiDefault/noDuplicate/schedule/restart/private404/cleanup')
}
smoke().catch(error => { console.error(error); process.exitCode = 1 })
