import test from 'node:test'
import assert from 'node:assert/strict'
import { monthCells, shiftMonth, beijingDate, millisecondsToBeijingMidnight, beijingClock } from '../src/checkIn.ts'

test('calendar aligns Monday-first, handles leap day and pads complete weeks', () => {
  const cells = monthCells('2024-02')
  assert.deepEqual(cells.slice(0, 4), [null, null, null, '2024-02-01'])
  assert.equal(cells.filter(Boolean).length, 29)
  assert.equal(cells.length % 7, 0)
  assert.ok(cells.includes('2024-02-29'))
})
test('month navigation crosses year boundaries and rejects invalid months', () => {
  assert.equal(shiftMonth('2026-01', -1), '2025-12')
  assert.equal(shiftMonth('2026-12', 1), '2027-01')
  assert.throws(() => monthCells('2026-13'))
})
test('Beijing date and midnight timer ignore the computer timezone', () => {
  const before = new Date('2026-10-17T15:59:59.000Z')
  assert.equal(beijingDate(before), '2026-10-17')
  assert.equal(millisecondsToBeijingMidnight(before), 1000)
  assert.equal(beijingDate(new Date('2026-10-17T16:00:00Z')), '2026-10-18')
  assert.equal(millisecondsToBeijingMidnight(new Date('2026-10-17T16:00:00Z')), 86400000)
  assert.equal(beijingClock('2026-10-17T16:03:00+00:00'), '00:03')
})
