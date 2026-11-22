import test from 'node:test'
import assert from 'node:assert/strict'
import { rankNetworkResults } from '../../src/knowledgeResultOrder.ts'

test('exact concept and alias precede related mentions without mutating results', () => {
  const rows = [
    { id: 'related', name: '偏导数', aliases: [] },
    { id: 'prefix', name: '导数与单调性', aliases: [] },
    { id: 'alias', name: '微商', aliases: ['导数'] },
    { id: 'exact', name: '导数', aliases: [] },
  ]
  assert.deepEqual(rankNetworkResults(rows, ' 导数 ').map(r => r.id), ['exact', 'alias', 'prefix', 'related'])
  assert.equal(rows[0].id, 'related')
})

test('empty queries retain server order and equivalent matches are stable', () => {
  const rows = [{ id: 'b', name: 'B', aliases: [] }, { id: 'a', name: 'A', aliases: [] }]
  assert.deepEqual(rankNetworkResults(rows, ''), rows)
  assert.deepEqual(rankNetworkResults(rows, 'unmatched'), rows)
})

test('matching is case insensitive and preserves result objects', () => {
  const rows = [{ id: '1', name: 'Other', aliases: [] }, { id: '2', name: 'Taylor', aliases: [] }]
  assert.equal(rankNetworkResults(rows, 'TAYLOR')[0], rows[1])
})
