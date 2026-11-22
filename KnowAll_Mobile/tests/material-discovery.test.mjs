import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeDocumentHits, visibleTreeNodes, normalizeTreeNodes } from '../src/materialDiscovery.ts'
test('real SQL chunk metadata becomes a document target, never the chunk ID', () => {
  const [hit] = normalizeDocumentHits([{ id: 'chunk-2', text: 'DNA保存信息', metadata: { doc_id: 'doc-1', filename: '课程.md', chunk_index: 1, page_range: '2–3' } }])
  assert.equal(hit.documentId, 'doc-1'); assert.equal(hit.chunkId, 'chunk-2'); assert.equal(hit.title, '课程.md'); assert.equal(hit.pageRange, '2–3')
})
test('filename-only SQL hit opens the document without inventing a chunk', () => {
  assert.equal(normalizeDocumentHits([{ id: 'document:doc-1', metadata: { doc_id: 'doc-1', filename: '课程' } }])[0].chunkId, '')
})
test('missing source IDs stay readable but cannot open a random document', () => {
  const [hit] = normalizeDocumentHits([{ id: 'chunk-only', text: '摘要' }]); assert.equal(hit.documentId, ''); assert.equal(hit.text, '摘要')
})
test('malformed result envelopes are rejected rather than claiming no matches', () => { assert.throws(() => normalizeDocumentHits({ results: null })); assert.deepEqual(normalizeDocumentHits({ results: [] }), []) })
test('collapsed branches hide descendants, duplicate node IDs keep distinct paths', () => {
  const roots = normalizeTreeNodes([{ id: 'same', label: '父', children: [{ id: 'same', label: '子' }] }, { id: 'same', label: '另一分支' }])
  assert.deepEqual(visibleTreeNodes(roots, new Set()).map(n => n.label), ['父', '另一分支'])
  const expanded = visibleTreeNodes(roots, new Set(['0'])); assert.equal(expanded[1].path, '0.0'); assert.equal(expanded[2].path, '1')
})
test('deep trees keep complete labels and bounded visual indent', () => {
  let node = { label: '终点', children: [] }; for (let i = 0; i < 100; i++) node = { label: '层'+i, children: [node] }
  const expanded = new Set(); let path = '0'; for (let i = 0; i < 100; i++) { expanded.add(path); path += '.0' }
  const rows = visibleTreeNodes(normalizeTreeNodes([node]), expanded); assert.equal(rows.length, 101); assert.equal(rows.at(-1).label, '终点'); assert.ok(rows.every(r => r.indent <= 24))
})
