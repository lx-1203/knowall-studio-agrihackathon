import test from 'node:test'
import assert from 'node:assert/strict'
import { createScreenPrivacyController } from '../src/screenPrivacy.ts'

function setup(overrides = {}) {
  const calls = { capture: 0, switcher: 0 }
  const controller = createScreenPrivacyController({
    isAvailable: async () => true,
    preventCapture: async () => { calls.capture++ },
    protectAppSwitcher: async () => { calls.switcher++ },
    ...overrides,
  })
  return { controller, calls }
}

test('business content stays hidden until native protection has finished', async () => {
  let release
  const { controller } = setup({ preventCapture: () => new Promise(resolve => { release = resolve }) })
  assert.equal(controller.getSnapshot().concealed, true)
  controller.setAppState('active')
  const ready = controller.ensureProtection()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(controller.getSnapshot().concealed, true)
  release()
  await ready
  assert.equal(controller.getSnapshot().status, 'protected')
  assert.equal(controller.getSnapshot().concealed, false)
})

test('unavailable native protection fails closed without enabling capture', async () => {
  const { controller, calls } = setup({ isAvailable: async () => false })
  controller.setAppState('active')
  await controller.ensureProtection()
  assert.equal(controller.getSnapshot().status, 'error')
  assert.equal(controller.getSnapshot().concealed, true)
  assert.equal(calls.capture, 0)
})

test('native rejection hides content and can be retried', async () => {
  let attempts = 0
  const { controller } = setup({ preventCapture: async () => {
    if (++attempts === 1) throw new Error('native error with private details')
  } })
  controller.setAppState('active')
  await controller.ensureProtection()
  assert.equal(controller.getSnapshot().status, 'error')
  assert.equal(controller.getSnapshot().concealed, true)
  assert.equal(JSON.stringify(controller.getSnapshot()).includes('private details'), false)
  await controller.ensureProtection()
  assert.equal(controller.getSnapshot().concealed, false)
})

test('concurrent and repeated mounts enable native protection only once', async () => {
  const { controller, calls } = setup()
  await Promise.all([controller.ensureProtection(), controller.ensureProtection()])
  await controller.ensureProtection()
  assert.equal(calls.capture, 1)
  assert.equal(calls.switcher, 1)
})

test('switcher failure stays hidden and retry does not repeat capture activation', async () => {
  let attempts = 0
  const { controller, calls } = setup({ protectAppSwitcher: async () => {
    if (++attempts === 1) throw new Error('switcher unavailable')
  } })
  controller.setAppState('active')
  await controller.ensureProtection()
  assert.equal(controller.getSnapshot().concealed, true)
  await controller.ensureProtection()
  assert.equal(controller.getSnapshot().concealed, false)
  assert.equal(calls.capture, 1)
  assert.equal(attempts, 2)
})

test('inactive, background and unknown states conceal synchronously', async () => {
  const { controller, calls } = setup()
  await controller.ensureProtection()
  controller.setAppState('active')
  assert.equal(controller.getSnapshot().concealed, false)
  for (const state of ['inactive', 'background', 'unknown', 'extension']) {
    controller.setAppState(state)
    assert.equal(controller.getSnapshot().concealed, true)
    assert.equal(controller.getSnapshot().status, 'protected')
  }
  controller.setAppState('active')
  assert.equal(controller.getSnapshot().concealed, false)
  assert.equal(calls.capture, 1)
})

test('subscriptions publish transitions, unsubscribe and keep snapshots stable between changes', async () => {
  const { controller } = setup()
  const snapshot = controller.getSnapshot()
  assert.equal(snapshot, controller.getSnapshot())
  const changes = []
  const unsubscribe = controller.subscribe(() => changes.push(controller.getSnapshot()))
  controller.setAppState('active')
  await controller.ensureProtection()
  assert.equal(changes.at(-1).concealed, false)
  unsubscribe()
  const count = changes.length
  controller.setAppState('background')
  assert.equal(changes.length, count)
})
