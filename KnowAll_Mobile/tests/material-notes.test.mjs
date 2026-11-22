import test from 'node:test'
import assert from 'node:assert/strict'
import { MaterialNoteSession, materialNoteKey } from '../src/materialNotes.ts'
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const memory = () => { const data = new Map(); return { data, getItem: async key => data.get(key) ?? null, setItem: async (key, text) => { data.set(key, text) } } }

test('notes keep existing account/document keys and never use an anonymous scope', () => {
  assert.equal(materialNoteKey('u1', 'bio'), 'material-note:u1:bio')
  assert.equal(materialNoteKey(undefined, 'bio'), null)
  assert.equal(materialNoteKey('u1', undefined), null)
})
test('load failures cannot silently overwrite a stored note with empty text', async () => {
  let fail = true; const writes = []
  const session = new MaterialNoteSession({ getItem: async () => { if (fail) throw Error('read'); return '原笔记' }, setItem: async (...values) => { writes.push(values) } }, 'a')
  assert.equal(await session.load(), false)
  session.update('误覆盖')
  assert.equal(await session.flush(), false)
  assert.deepEqual(writes, [])
  fail = false
  assert.equal(await session.load(), true)
  assert.equal(session.snapshot().text, '原笔记')
})
test('closing during a save drains new edits in order instead of overwriting them with an older snapshot', async () => {
  const gate = deferred(); const writes = []
  const session = new MaterialNoteSession({ getItem: async () => '', setItem: async (_key, text) => { writes.push(text); if (writes.length === 1) await gate.promise } }, 'a')
  await session.load(); session.update('第一版')
  const saving = session.flush()
  session.update('连续输入后的最新版')
  const closing = session.flush()
  gate.resolve()
  assert.equal(await saving, true); assert.equal(await closing, true)
  assert.deepEqual(writes, ['第一版', '连续输入后的最新版'])
  assert.equal(session.snapshot().dirty, false)
  assert.equal(session.snapshot().text, '连续输入后的最新版')
})
test('save failure retains dirty text; a retry commits the same draft including a cleared note', async () => {
  let fail = true; const store = memory()
  store.data.set('a', '原笔记')
  const session = new MaterialNoteSession({ ...store, setItem: async (key, text) => { if (fail) throw Error('full'); store.data.set(key, text) } }, 'a')
  await session.load(); session.update('')
  assert.equal(await session.flush(), false)
  assert.equal(session.snapshot().dirty, true)
  assert.equal(session.snapshot().text, '')
  assert.equal(store.data.get('a'), '原笔记')
  fail = false
  assert.equal(await session.flush(), true)
  assert.equal(store.data.get('a'), '')
  assert.equal(session.snapshot().error, '')
})
test('delayed loading/saving of an old account or document never writes into the new scope', async () => {
  const store = memory(); const gate = deferred()
  store.data.set('u1:bio', '生物原笔记'); store.data.set('u2:history', '另一账号历史笔记')
  const old = new MaterialNoteSession({ ...store, getItem: async key => { await gate.promise; return store.getItem(key) } }, 'u1:bio')
  const loading = old.load()
  const next = new MaterialNoteSession(store, 'u2:history')
  await next.load(); next.update('历史新笔记'); await next.flush()
  gate.resolve(); await loading
  assert.equal(next.snapshot().text, '历史新笔记')
  old.update('生物更新'); await old.flush()
  assert.equal(store.data.get('u2:history'), '历史新笔记')
  assert.equal(store.data.get('u1:bio'), '生物更新')
})
test('reloading an already edited session never erases its draft', async () => {
  const session = new MaterialNoteSession(memory(), 'a')
  await session.load(); session.update('正在编辑')
  assert.equal(await session.load(), false)
  assert.equal(session.snapshot().text, '正在编辑')
})
test('reverting text while a write is pending still restores the final text on disk', async () => {
  const gate = deferred(); const store = memory(); const writes = []
  store.data.set('a', '原笔记')
  const session = new MaterialNoteSession({ ...store, setItem: async (key, text) => {
    writes.push(text); if (writes.length === 1) await gate.promise; store.data.set(key, text)
  } }, 'a')
  await session.load(); session.update('中间版本'); const saving = session.flush()
  session.update('原笔记')
  assert.equal(session.snapshot().saving, true)
  gate.resolve(); assert.equal(await saving, true)
  assert.deepEqual(writes, ['中间版本', '原笔记'])
  assert.equal(store.data.get('a'), '原笔记')
})
