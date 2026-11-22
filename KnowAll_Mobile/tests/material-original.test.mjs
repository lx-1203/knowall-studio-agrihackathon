import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bytesDataUri, originalKind, originalPosition } from '../src/materialOriginal.ts'

test('authenticated preview bytes preserve exact binary without browser globals', () => {
  for (const length of [0, 1, 2, 3, 256, 80001]) {
    const bytes = Uint8Array.from({ length }, (_, i) => i % 256)
    assert.equal(bytesDataUri(bytes.buffer), `data:image/png;base64,${Buffer.from(bytes).toString('base64')}`)
  }
})
test('original position validates saved values against actual page count', () => {
  assert.deepEqual(originalPosition({ page: 500, zoom: 2 }, 3), { page: 3, zoom: 2 })
  assert.deepEqual(originalPosition({ page: -1, zoom: 12 }, 3), { page: 1, zoom: 1 })
  assert.deepEqual(originalPosition({ page: '2', zoom: 1.5 }, 3), { page: 1, zoom: 1.5 })
  assert.deepEqual(originalPosition(null, 2), { page: 1, zoom: 1 })
})
test('PDF and uploaded images have previews; text files keep parsed reading', () => {
  assert.equal(originalKind('PDF'), 'pdf')
  assert.equal(originalKind('jpeg'), 'image')
  assert.equal(originalKind('md'), null)
})
