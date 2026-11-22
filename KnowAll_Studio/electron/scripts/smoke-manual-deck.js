// Real packaged client, empty account, extracted cloud. No HTTP mocks/paid calls.
const { startCloud, request, launchClient } = require('./smoke-flashcard-export')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
async function smoke() {
  const output = process.env.KNOWALL_EXPORT_SMOKE_OUTPUT
  assert.ok(output && process.env.KNOWALL_DESKTOP_EXECUTABLE && process.env.KNOWALL_TEST_BACKEND)
  await fs.mkdir(output, { recursive: true })
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-first-deck-'))
  const cloud = startCloud()
  let client, page, report
  try {
    const ready = await cloud.event('READY')
    const opened = await launchClient(process.env.KNOWALL_DESKTOP_EXECUTABLE, data, ready.origin, ready.accounts[1]); client = opened.client; page = opened.page
    assert.deepEqual((await request(page, '/api/v1/documents/')).data, [])
    assert.deepEqual((await request(page, '/api/v1/flashcards/decks')).data, [])
    await page.goto(new URL('/flashcards', page.url()).href)
    await page.getByRole('button', { name: '创建我的第一个牌组', exact: true }).click()
    const name = '我的首个生物牌组', description = '无资料也能开始学习'
    await page.getByLabel('牌组名称', { exact: true }).fill(name)
    await page.getByLabel('说明（可选）', { exact: true }).fill(description)
    await page.screenshot({ path: path.join(output, 'first-deck-form.png'), animations: 'disabled' })
    const sent = page.waitForResponse(r => r.url().endsWith('/flashcards/decks') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '创建并添加卡片', exact: true }).click()
    const response = await sent; assert.equal(response.status(), 201)
    assert.deepEqual(response.request().postDataJSON(), { name, description })
    const deck = await response.json(), cards = []
    const fronts = ['细胞呼吸有什么作用？', '{{c1::线粒体}} 为细胞供能', '比较细胞核与线粒体的作用']
    const backs = ['**手工知识**\n\n- 细胞呼吸\n- 能量供应', '线粒体参与细胞呼吸', '| 结构 | 功能 |\n| --- | --- |\n| 细胞核 | 遗传物质 |\n| 线粒体 | 供能 |']
    for (const [index, kind] of ['qa', 'cloze', 'compare'].entries()) {
      if (index) await page.getByRole('row').filter({ hasText: name }).getByRole('button', { name: '添加卡片', exact: true }).click()
      await page.getByRole('dialog', { name: '手动添加卡片', exact: true }).waitFor()
      if (index) {
        await page.getByRole('dialog').getByText('问答卡', { exact: true }).click()
        await page.getByText(index === 1 ? '填空卡' : '对比卡', { exact: true }).click()
      }
      await page.getByLabel('问题 / 正面', { exact: true }).fill(fronts[index])
      await page.getByLabel('答案 / 解释', { exact: true }).fill(backs[index])
      const saved = page.waitForResponse(r => r.url().endsWith('/flashcards/cards') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '保存卡片', exact: true }).click()
      const actual = await saved; assert.equal(actual.status(), 201)
      assert.equal(actual.request().postDataJSON().card_type, kind)
      assert.equal(actual.request().postDataJSON().deck_id, deck.id)
      cards.push(await actual.json())
      await page.getByRole('dialog').waitFor({ state: 'detached' })
      await page.getByTestId('flashcard-session-progress').filter({ hasText: `待复习 ${index + 1} 张` }).waitFor()
    }
    assert.equal((await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id)).data.cards.length, 3)
    await page.screenshot({ path: path.join(output, 'three-card-ready.png'), animations: 'disabled' })
    for (let i = 0; i < 3; i++) {
      const good = page.getByRole('button', { name: '正常回忆 (3)', exact: true })
      if (!await good.isVisible()) await page.locator('.flashcard-flip').click()
      const graded = page.waitForResponse(r => r.url().endsWith('/flashcards/review'))
      await good.click(); assert.equal((await (await graded).json()).review_count, 1)
      if (i < 2) await page.getByTestId('flashcard-session-progress').filter({ hasText: `本轮已复习 ${i + 1} 张，待复习 ${2 - i} 张` }).waitFor()
    }
    await page.getByText('已复习 3 张卡片', { exact: true }).waitFor()
    await page.screenshot({ path: path.join(output, 'first-deck-completed.png'), animations: 'disabled' })
    cloud.command('recycle'); await cloud.event('RECYCLED')
    const due = await request(page, '/api/v1/flashcards/due?deck_id=' + deck.id)
    assert.equal(due.status, 200); assert.deepEqual(due.data.cards, []); assert.equal(due.data.due_count, 0)
    const decks = await request(page, '/api/v1/flashcards/decks'); assert.equal(decks.data.find(d => d.id === deck.id).card_count, 3)
    const exported = await page.request.get(new URL('/api/v1/flashcards/export/anki/' + deck.id, page.url()).href, { headers: { Authorization: 'Bearer ' + await page.evaluate(() => localStorage.getItem('knowall_token')) } })
    assert.equal(exported.status(), 200)
    const apkg = path.join(data, 'first-deck.apkg'); await fs.writeFile(apkg, await exported.body())
    const check = spawnSync(process.env.KNOWALL_TEST_PYTHON, ['-c', `
import sys,genanki
from anki.collection import Collection,ImportAnkiPackageRequest
c=Collection(sys.argv[1])
try:
 for _ in range(2):c.import_anki_package(ImportAnkiPackageRequest(package_path=sys.argv[2]))
 assert c.note_count()==c.card_count()==3
 assert any(d.name.startswith('我的首个生物牌组 [') for d in c.decks.all_names_and_ids())
 n=[c.get_note(i) for i in c.find_notes('') if c.get_note(i).guid==genanki.guid_for('knowall-note-v1',sys.argv[3],sys.argv[4])][0]
 assert '<li>细胞呼吸</li>' in n.fields[1]
finally:c.close()
print('PASS actualAnki2imports3notes3cards firstDeck')
`, path.join(data, 'consumer.anki2'), apkg, deck.id, cards[0].id], { encoding: 'utf8', windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } })
    assert.equal(check.status, 0, check.stderr)
    const denial = await page.evaluate(async ({ account, deckId, cardId }) => {
      const login = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(account) }); const auth = await login.json()
      const headers = { 'content-type': 'application/json', Authorization: 'Bearer ' + auth.access_token }
      const statuses = []
      for (const [url, body] of [['/api/v1/flashcards/cards', { deck_id: deckId, card_type: 'qa', front: '禁止他人写入卡片', back: '私有' }], ['/api/v1/flashcards/review', { card_id: cardId, rating: 3 }], ['/api/v1/flashcards/export/anki/' + deckId, undefined]]) {
        const result = await fetch(url, { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined }); statuses.push(result.status)
      }
      return { login: login.status, statuses }
    }, { account: ready.accounts[0], deckId: deck.id, cardId: cards[0].id })
    assert.deepEqual(denial, { login: 200, statuses: [404, 404, 404] })
    await fs.copyFile(apkg, path.join(output, 'first-deck.apkg'))
    report = { passed: true, checked_at: new Date().toISOString(), actual_executable: process.env.KNOWALL_DESKTOP_EXECUTABLE, actual_backend: process.env.KNOWALL_TEST_BACKEND,
      actual_empty_account: true, actual_first_deck_ui: true, actual_three_types_ui: true, actual_three_card_completed: true, actual_restarted_due_empty: true, actual_anki_two_imports_three_cards: true, other_account: denial, deck_id: deck.id, cards, no_paid_call: true, no_http_mocks: true }
  } catch (error) { if (page) await page.screenshot({ path: path.join(output, 'failed.png'), animations: 'disabled' }).catch(() => {}); throw error }
  finally {
    try { if (client) await client.close() } finally { await cloud.close(); assert.equal(path.dirname(path.resolve(data)), path.resolve(os.tmpdir())); assert.ok(path.basename(data).startsWith('knowall-first-deck-')); await fs.rm(data, { recursive: true, force: true }) }
  }
  report.temporary_data_removed = true
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log('PASS newWindows/extracted-cloud emptyAccount/firstDeck/threeTypes/3grades/restart/realAnki/private404/cleanup')
}
smoke().catch(error => { console.error(error); process.exitCode = 1 })
