import test from 'node:test'
import assert from 'node:assert/strict'
const { MaterialProgressSession: Session, materialProgressKey: key } = await import('../src/materialProgress.ts').catch(() => ({}))
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const memory = () => { const data = new Map(); return { data, getItem: async key => data.get(key) ?? null, setItem: async (key, text) => { data.set(key, text) } } }
const saved = (index = 2, fontSize = 24) => JSON.stringify({ index, fontSize })
test('first remote restore can replace an unedited default but never a user navigation', async () => {
  const store=memory(),session=new Session(store,'cloud')
  session.activate(5); await session.load(); store.data.set('cloud',saved(3,18))
  assert.equal(await session.reloadSaved(),true); assert.equal(session.snapshot().index,3)
  session.update({index:1}); store.data.set('cloud',saved(4,18))
  assert.equal(await session.reloadSaved(),false); assert.equal(session.snapshot().index,1)
})

test('progress keys retain legacy account/document isolation without anonymous storage', () => {
  assert.equal(key('u1', 'bio'), 'material-progress:u1:bio')
  assert.equal(key(undefined, 'bio'), null); assert.equal(key('u1'), null)
})
test('failed progress read permits reading but never overwrites the stored position', async () => {
  let fail = true; const store = memory(); store.data.set('a', saved())
  const session = new Session({ ...store, getItem: async k => { if (fail) throw Error('read'); return store.getItem(k) } }, 'a')
  session.activate(5); assert.equal(await session.load(), false)
  session.update({ index: 1 }); assert.equal(session.snapshot().index, 1)
  assert.equal(await session.flush(), false); assert.equal(store.data.get('a'), saved())
  fail = false; assert.equal(await session.load(), true)
  assert.equal(session.snapshot().index, 1); assert.equal(session.snapshot().fontSize, 24)
  assert.equal(await session.flush(), true); assert.equal(store.data.get('a'), saved(1))
})
test('retry with no reading changes restores old position and does not rewrite it', async () => {
  let fail = true; const writes = []; const store = { getItem: async () => { if (fail) throw Error('read'); return saved() }, setItem: async (...args) => { writes.push(args) } }
  const session = new Session(store, 'a'); session.activate(5)
  await session.load(); fail = false; await session.load(); await session.flush()
  assert.equal(session.snapshot().index, 2); assert.equal(session.snapshot().fontSize, 24); assert.deepEqual(writes, [])
})
test('malformed and invalid legacy records remain untouched, only a successful reread unlocks writes', async () => {
  for (const raw of ['{', 'null', '{}', saved(-1), saved(1, 100), '{"index":1.2,"fontSize":18}']) {
    const store = memory(); store.data.set('a', raw); const session = new Session(store, 'a'); session.activate(5)
    assert.equal(await session.load(), false); session.update({ fontSize: 20 })
    assert.equal(await session.flush(), false); assert.equal(store.data.get('a'), raw)
    store.data.set('a', saved()); assert.equal(await session.load(), true)
    assert.equal(session.snapshot().index, 2); assert.equal(session.snapshot().fontSize, 20)
    await session.flush(); assert.equal(store.data.get('a'), saved(2, 20))
  }
})
test('slow read merges only fields explicitly changed while waiting', async () => {
  const gate = deferred(); const store = memory(); store.data.set('a', saved())
  const session = new Session({ ...store, getItem: async k => { await gate.promise; return store.getItem(k) } }, 'a')
  const loading = session.load(); session.activate(5); session.update({ fontSize: 20 }); gate.resolve(); await loading
  assert.equal(session.snapshot().index, 2); assert.equal(session.snapshot().fontSize, 20)
})
test('rapid navigation during a pending save drains latest position in order', async () => {
  const gate = deferred(); const started = deferred(); const store = memory(); const writes = []
  store.setItem = async (key, text) => { writes.push(text); if (writes.length === 1) { started.resolve(); await gate.promise }; store.data.set(key, text) }
  const session = new Session(store, 'a'); session.activate(5); await session.load()
  session.update({ index: 1 }); const first = session.flush(); await started.promise; session.update({ index: 3, fontSize: 22 }); const second = session.flush()
  gate.resolve(); assert.equal(await first, true); assert.equal(await second, true)
  assert.deepEqual(writes, [saved(1, 18), saved(3, 22)]); assert.equal(session.snapshot().dirty, false)
})
test('moving back to the saved position during a write still restores final disk position', async () => {
  const gate = deferred(); const started = deferred(); const store = memory(); store.data.set('a', saved(0, 18))
  const set = store.setItem; store.setItem = async (key, text) => { started.resolve(); await gate.promise; await set(key, text) }
  const session = new Session(store, 'a'); session.activate(5); await session.load()
  session.update({ index: 1 }); const writing = session.flush(); await started.promise; session.update({ index: 0 }); gate.resolve(); await writing
  assert.equal(store.data.get('a'), saved(0, 18))
})
test('save failures retain position until manual retry, without automatic replay', async () => {
  let fail = true, writes = 0; const store = memory(); store.data.set('a', saved()); const set = store.setItem
  store.setItem = async (key, text) => { writes++; if (fail) throw Error('full'); await set(key, text) }
  const session = new Session(store, 'a'); session.activate(5); await session.load(); session.update({ index: 4 })
  assert.equal(await session.flush(), false); await new Promise(r => setTimeout(r, 10))
  assert.equal(writes, 1); assert.equal(session.snapshot().dirty, true); assert.equal(store.data.get('a'), saved())
  fail = false; assert.equal(await session.flush(), true); assert.equal(store.data.get('a'), saved(4))
})
test('reopened session waits for prior session writes to avoid restoring stale disk position', async () => {
  const gate = deferred(); const store = memory(); store.data.set('a', saved()); const set = store.setItem
  store.setItem = async (key, text) => { await gate.promise; await set(key, text) }
  const previous = new Session(store, 'a'); previous.activate(5); await previous.load(); previous.update({ index: 4 }); const writing = previous.flush()
  const next = new Session(store, 'a'); next.activate(5); const loading = next.load()
  gate.resolve(); await writing; await loading
  assert.equal(next.snapshot().index, 4); assert.equal(store.data.get('a'), saved(4))
})
test('invalid navigation cannot create an invalid record; fewer chunks clamp only after activation', async () => {
  const store = memory(); store.data.set('a', saved(20)); const session = new Session(store, 'a')
  await session.load(); assert.equal(await session.flush(), false); assert.equal(store.data.get('a'), saved(20))
  session.activate(3); session.update({ index: -1, fontSize: NaN }); await session.flush()
  assert.equal(session.snapshot().index, 2); assert.equal(store.data.get('a'), saved(2))
})
test('delayed old account save is scoped and cannot touch a new account or document', async () => {
  const gate = deferred(); const store = memory(); const set = store.setItem
  store.setItem = async (key, text) => { if (key === 'u1:bio') await gate.promise; await set(key, text) }
  const old = new Session(store, 'u1:bio'); old.activate(5); await old.load(); old.update({ index: 4 }); const writing = old.flush()
  const next = new Session(store, 'u2:history'); next.activate(3); await next.load(); next.update({ index: 2, fontSize: 20 }); await next.flush()
  gate.resolve(); await writing
  assert.equal(store.data.get('u2:history'), saved(2, 20)); assert.equal(store.data.get('u1:bio'), saved(4, 18))
})
test('explicit replacement recovers a corrupt record and a failed replacement still retains the old bytes', async () => {
  let fail = true; const store = memory(); store.data.set('a', '{'); const set = store.setItem
  store.setItem = async (key, text) => { if (fail) throw Error('full'); await set(key, text) }
  const session = new Session(store, 'a'); session.activate(5); await session.load(); session.update({ index: 3, fontSize: 22 })
  assert.equal(await session.replaceWithCurrent(), false); assert.equal(store.data.get('a'), '{')
  fail = false; assert.equal(await session.flush(), true); assert.equal(store.data.get('a'), saved(3, 22))
  const restored = new Session(store, 'a'); restored.activate(5); await restored.load()
  assert.equal(restored.snapshot().index, 3); assert.equal(restored.snapshot().fontSize, 22)
})
