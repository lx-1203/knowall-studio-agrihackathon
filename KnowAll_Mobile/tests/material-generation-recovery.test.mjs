import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as recovery from '../src/materialGenerationRecovery.ts'
const { recoveryKey, loadRecovery, startRecovery, clearRecovery, confirmRecovery, CorruptRecoveryError, discardCorruptRecovery } = recovery

const scope = { accountId: 'me', documentId: 'bio' }
const settings = { question_count: 2, card_count: 1, model: 'fixture', question_types: ['single_choice'] }
const store = () => { const data = new Map(); return { data, getItem: async key => data.get(key) ?? null, setItem: async (key, value) => { data.set(key, value) }, removeItem: async key => { data.delete(key) } } }
const receipt = record => ({ id: 'new', document_id: 'bio', source_hash: 'source', stale: false, sections: [{ id: 's', notes: '有效内容' }], config: { ...record.settings, concurrency: 2, max_section_chars: 6000 } })
test('scope is mandatory and encoded independently without delimiter collision', () => {
  assert.throws(() => recoveryKey({ ...scope, accountId: '' }))
  assert.notEqual(recoveryKey({ accountId: 'a:b', documentId: 'c' }), recoveryKey({ accountId: 'a', documentId: 'b:c' }))
})
test('a durable submission retains exact configuration, source, previous receipt and scope across reload', async () => {
  const s = store(); assert.equal(await loadRecovery(s, scope), null)
  const first = await startRecovery(s, scope, settings, 'source', 'old')
  assert.deepEqual(await loadRecovery(s, scope), first)
  assert.equal(first.beforeId, 'old'); assert.deepEqual(first.settings, settings)
  assert.equal(await loadRecovery(s, { ...scope, documentId: 'history' }), null)
  assert.equal(await loadRecovery(s, { ...scope, accountId: 'other' }), null)
})
test('invalid or corrupt bytes cannot be overwritten by starting a submission', async () => {
  const s = store(), k = recoveryKey(scope), record = await startRecovery(s, scope, settings, 'source')
  for (const raw of ['{', 'null', JSON.stringify({ ...record, version: 9 }), JSON.stringify({ ...record, accountId: 'other' }), JSON.stringify({ ...record, sourceHash: '' }), JSON.stringify({ ...record, settings: { ...settings, question_count: 101 } })]) {
    s.data.set(k, raw)
    await assert.rejects(loadRecovery(s, scope)); await assert.rejects(startRecovery(s, scope, settings, 'source'))
    assert.equal(s.data.get(k), raw)
  }
})
test('storage read/write failure rejects before a network caller can dispatch', async () => {
  const s = store(); let network = 0
  s.setItem = async () => { throw Error('full') }
  await assert.rejects((async () => { await startRecovery(s, scope, settings, 'source'); network++ })())
  assert.equal(network, 0); assert.equal(await loadRecovery(s, scope), null)
  s.getItem = async () => { throw Error('unavailable') }
  await assert.rejects(loadRecovery(s, scope)); await assert.rejects(startRecovery(s, scope, settings, 'source'))
})
test('concurrent starts on one key cannot replace the first pending submission', async () => {
  const s = store(), results = await Promise.allSettled([startRecovery(s, scope, settings, 'source'), startRecovery(s, scope, settings, 'source')])
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1)
  assert.equal(results.filter(r => r.status === 'rejected').length, 1)
  assert.deepEqual(await loadRecovery(s, scope), results.find(r => r.status === 'fulfilled').value)
})
test('late completion only clears its own id and never deletes a newer submission', async () => {
  const s = store(), first = await startRecovery(s, scope, settings, 'source')
  assert.equal(await clearRecovery(s, scope, first.id), true)
  const second = await startRecovery(s, scope, settings, 'source')
  assert.notEqual(first.id, second.id)
  assert.equal(await clearRecovery(s, scope, first.id), false)
  assert.deepEqual(await loadRecovery(s, scope), second)
})
test('failed deletion retains original bytes and can be explicitly retried', async () => {
  const s = store(), record = await startRecovery(s, scope, settings, 'source'), raw = s.data.get(recoveryKey(scope)), remove = s.removeItem
  s.removeItem = async () => { throw Error('unavailable') }
  await assert.rejects(clearRecovery(s, scope, record.id)); assert.equal(s.data.get(recoveryKey(scope)), raw)
  s.removeItem = remove; assert.equal(await clearRecovery(s, scope, record.id), true); assert.equal(await loadRecovery(s, scope), null)
})
test('recovery requires source/config/document freshness and refuses the prior pack', async () => {
  const s = store(), record = await startRecovery(s, scope, settings, 'source', 'old'), value = receipt(record)
  assert.equal(confirmRecovery(value, record).status, 'complete')
  for (const invalid of [null, { ...value, id: 'old' }, { ...value, source_hash: 'changed' }, { ...value, stale: true }, { ...value, document_id: 'history' }, { ...value, config: { ...value.config, model: 'other' } }]) assert.throws(() => confirmRecovery(invalid, record))
  assert.deepEqual(await loadRecovery(s, scope), record)
})
test('terminal all-failed and partial receipts are distinguishable without another POST', async () => {
  const s = store(), record = await startRecovery(s, scope, settings, 'source'), value = receipt(record)
  assert.equal(confirmRecovery({ ...value, sections: [{ id: 's', errors: ['failed'] }] }, record).status, 'failed')
  assert.equal(confirmRecovery({ ...value, sections: [{ id: 's', notes: '可用', errors: ['不足'] }] }, record).status, 'partial')
})
test('only parsed corrupt bytes expose a snapshot; read faults remain ordinary errors', async () => {
  const s = store(), key = recoveryKey(scope)
  for (const raw of ['{', '', 'null', '{"version":9}']) {
    s.data.set(key, raw)
    await assert.rejects(loadRecovery(s, scope), error => error instanceof CorruptRecoveryError && error.raw === raw)
    assert.equal(s.data.get(key), raw)
  }
  s.getItem = async () => { throw Error('Read failed') }
  await assert.rejects(loadRecovery(s, scope), error => !(error instanceof CorruptRecoveryError))
})
test('confirmed corrupt cleanup preserves changed bytes and valid submissions', async () => {
  const s = store(), key = recoveryKey(scope)
  s.data.set(key, '{')
  assert.equal(await discardCorruptRecovery(s, scope, '{'), true)
  const valid = await startRecovery(s, scope, settings, 'source')
  assert.equal(await discardCorruptRecovery(s, scope, '{'), false)
  assert.equal(await discardCorruptRecovery(s, scope, s.data.get(key)), false)
  assert.deepEqual(await loadRecovery(s, scope), valid)
  s.data.set(key, 'different broken bytes')
  assert.equal(await discardCorruptRecovery(s, scope, '{'), false)
  assert.equal(s.data.get(key), 'different broken bytes')
})
test('cleanup faults retain bytes and another account is never affected', async () => {
  const s = store(), key = recoveryKey(scope), other = { ...scope, accountId: 'other' }
  s.data.set(key, '{'); s.data.set(recoveryKey(other), '{')
  const remove = s.removeItem
  s.removeItem = async () => { throw Error('Delete failed') }
  await assert.rejects(discardCorruptRecovery(s, scope, '{')); assert.equal(s.data.get(key), '{')
  s.removeItem = remove
  assert.equal(await discardCorruptRecovery(s, scope, '{'), true)
  assert.equal(s.data.get(recoveryKey(other)), '{')
  const read = s.getItem; s.getItem = async () => { throw Error('Read failed') }
  await assert.rejects(discardCorruptRecovery(s, other, '{'))
  s.getItem = read; assert.equal(s.data.get(recoveryKey(other)), '{')
})
test('a new submission queued before corrupt cleanup is preserved', async () => {
  const s = store()
  const creating = startRecovery(s, scope, settings, 'source')
  const cleaning = discardCorruptRecovery(s, scope, '{')
  const valid = await creating
  assert.equal(await cleaning, false)
  assert.deepEqual(await loadRecovery(s, scope), valid)
})
