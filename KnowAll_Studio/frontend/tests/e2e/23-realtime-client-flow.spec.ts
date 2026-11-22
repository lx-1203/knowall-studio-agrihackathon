import { test, expect, E2E_TOKEN } from './fixtures'

test('global sync provider forwards the current account token', async ({ page }) => {
  const urls: string[] = []
  await page.routeWebSocket(/\/ws\/sync/, ws => { urls.push(ws.url()) })
  await page.goto('/')
  await expect.poll(() => urls.length).toBeGreaterThan(0)
  expect(new URL(urls[urls.length - 1]).searchParams.get('token')).toBe(E2E_TOKEN)
})

test('persisted sync diff is delivered as the same operation shape as live messages', async ({ page }) => {
  await page.routeWebSocket(/\/ws\/probe/, ws => ws.onMessage(raw => {
    const request = JSON.parse(String(raw))
    if (request.type === 'reconnect') ws.send(JSON.stringify({
      type: 'sync_diff', data: { version: 2, ops: [{ version: 2, op: 'insert', user_id: 'other',
        data: { operation: 'insert', position: 1, value: 'B', path: ['text'] } }] },
    }))
  }))
  await page.goto('/')
  await page.evaluate(async () => {
    const { RealtimeSyncClient } = await import('/src/services/RealtimeSyncClient.ts')
    ;(window as any).syncReceived = []
    const client = new RealtimeSyncClient({ docId: 'document', userId: 'current', userName: 'Current',
      url: `ws://${location.host}/ws/probe`, onMessage: msg => (window as any).syncReceived.push(msg) })
    ;(window as any).probeClient = client
    client.connect()
  })
  await expect.poll(() => page.evaluate(() => (window as any).syncReceived.length)).toBe(1)
  const result = await page.evaluate(() => {
    const client = (window as any).probeClient
    const output = { message: (window as any).syncReceived[0], version: client.getVersion() }
    client.disconnect()
    return output
  })
  expect(result.version).toBe(2)
  expect(result.message).toMatchObject({ type: 'operation', version: 2, user_id: 'other',
    data: { operation: 'insert', position: 1, value: 'B', path: ['text'] } })
})

test('socket loss during offline replay retains remaining operations for next connection', async ({ page }) => {
  await page.goto('/')
  const output = await page.evaluate(async () => {
    const { RealtimeSyncClient } = await import('/src/services/RealtimeSyncClient.ts')
    const sockets: any[] = []
    const NativeSocket = window.WebSocket
    // A controlled transport closes synchronously after one write, exposing replay races.
    class Transport {
      static OPEN = 1
      static CONNECTING = 0
      readyState = 0
      onopen: any
      onclose: any
      onmessage: any
      onerror: any
      sent: any[] = []
      constructor() { sockets.push(this) }
      send(raw: string) {
        const msg = JSON.parse(raw)
        this.sent.push(msg)
        if (msg.type === 'operation' && sockets.length === 1) this.readyState = 3
      }
      close() { this.readyState = 3 }
      open() { this.readyState = 1; this.onopen?.({}) }
      ack() { this.onmessage?.({ data: '{"type":"ack","data":{"new_version":0}}' }) }
    }
    window.WebSocket = Transport as any
    const client = new RealtimeSyncClient({ docId: 'document', userId: 'current', userName: 'Current' })
    try {
      client.sendOperation({ operation: 'insert', value: 'A' })
      client.sendOperation({ operation: 'insert', value: 'B' })
      client.connect()
      sockets[0].open()
      sockets[0].ack()
      client.disconnect()
      client.connect()
      sockets[1].open()
      sockets[1].ack()
      return sockets.map(socket => socket.sent.filter((msg: any) => msg.type === 'operation').map((msg: any) => msg.data.value))
    } finally {
      client.disconnect()
      window.WebSocket = NativeSocket
    }
  })
  expect(output).toEqual([['A'], ['B']])
})

for (const code of [4001, 4003, 4401, 4403]) {
  test(`terminal close ${code} stops retry and reports its cause`, async ({ page }) => {
    let attempts = 0
    await page.clock.install()
    await page.routeWebSocket(/\/ws\/denied/, ws => { attempts++; ws.close({ code }) })
    await page.goto('/')
    await page.evaluate(async () => {
      const { RealtimeSyncClient } = await import('/src/services/RealtimeSyncClient.ts')
      ;(window as any).syncErrors = []
      const client = new RealtimeSyncClient({ docId: 'exam', userId: 'current', userName: 'Current',
        url: `ws://${location.host}/ws/denied`, onError: err => (window as any).syncErrors.push(err) })
      ;(window as any).probeClient = client
      client.connect()
    })
    await expect.poll(() => page.evaluate(() => (window as any).syncErrors.length)).toBeGreaterThan(0)
    const errors = await page.evaluate(() => (window as any).syncErrors)
    expect(errors[errors.length - 1].code).toBe([4003, 4403].includes(code) ? 'FORBIDDEN' : 'AUTH_FAILED')
    await page.clock.runFor(65000)
    expect(attempts).toBe(1)
    await page.evaluate(() => (window as any).probeClient.disconnect())
  })
}
