const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const electronDir = path.resolve(__dirname, '..')
const source = fs.readFileSync(path.join(electronDir, 'main.js'), 'utf8')

function loadMain({ portAvailable = true, healthStatus = 200, appReady = false, exitOnSpawn = false } = {}) {
  const calls = { listen: [], spawn: [], health: [], windows: 0, quit: 0, errors: [] }
  const child = new EventEmitter()
  child.stdout = new EventEmitter()
  child.stderr = new EventEmitter()
  child.exitCode = null
  child.signalCode = null
  child.pid = 1234

  const net = {
    createServer() {
      const server = new EventEmitter()
      server.listen = (port, host, onListening) => {
        calls.listen.push({ port, host })
        queueMicrotask(() => {
          if (portAvailable) onListening()
          else server.emit('error', new Error('EADDRINUSE'))
        })
      }
      server.close = (onClose) => queueMicrotask(onClose)
      return server
    },
  }
  const http = {
    get(url, onResponse) {
      calls.health.push(url)
      const request = new EventEmitter()
      request.setTimeout = () => {}
      request.destroy = () => {}
      queueMicrotask(() => onResponse({ statusCode: healthStatus }))
      return request
    },
  }
  class BrowserWindow {
    constructor() {
      calls.windows++
      this.webContents = { openDevTools: () => {}, setWindowOpenHandler: () => {} }
    }
    once() {}
    on() {}
    loadURL() {}
    loadFile() {}
  }
  BrowserWindow.getAllWindows = () => []
  const electron = {
    app: {
      whenReady: () => appReady ? Promise.resolve() : new Promise(() => {}),
      on: () => {},
      quit: () => { calls.quit++ },
    },
    BrowserWindow,
    shell: {},
    ipcMain: { handle: () => {} },
    dialog: { showErrorBox: (...args) => calls.errors.push(args) },
  }
  const context = {
    __dirname: electronDir,
    console: { log: () => {}, warn: () => {}, error: () => {} },
    process: { env: {}, platform: 'win32' },
    setTimeout,
    require(name) {
      if (name === 'electron') return electron
      if (name === 'path') return path
      if (name === 'net') return net
      if (name === 'http') return http
      if (name === 'child_process') {
        return {
          execSync: () => {},
          spawn: (...args) => {
            calls.spawn.push(args)
            if (exitOnSpawn) queueMicrotask(() => {
              child.exitCode = 1
              child.emit('close', 1)
            })
            return child
          },
        }
      }
      throw new Error(`Unexpected require: ${name}`)
    },
  }
  vm.runInNewContext(source, context, { filename: 'main.js' })
  return { context, calls, child }
}

test('Electron binds and checks the KnowAll backend on 8001', async () => {
  const { context, calls } = loadMain()
  assert.equal(await context.startBackend(), true)
  assert.deepEqual(calls.listen, [{ port: 8001, host: '127.0.0.1' }])
  assert.equal(calls.spawn.length, 1)
  assert.equal(calls.spawn[0][1].at(-1), '8001')
  assert.equal(await context.waitForBackend(1, 0), true)
  assert.deepEqual(calls.health, ['http://127.0.0.1:8001/health'])
})

test('an occupied backend port stops Electron from spawning another server', async () => {
  const { context, calls } = loadMain({ portAvailable: false })
  assert.equal(await context.startBackend(), false)
  assert.equal(calls.spawn.length, 0)
  assert.equal(calls.health.length, 0)
})

test('a healthy unrelated server cannot satisfy a failed backend launch', async () => {
  const { context, calls, child } = loadMain()
  assert.equal(await context.startBackend(), true)
  child.exitCode = 1
  child.emit('close', 1)
  assert.equal(await context.waitForBackend(1, 0), false)
  assert.equal(calls.health.length, 0)
})

test('an occupied port stops startup without opening a frontend window', async () => {
  const { calls } = loadMain({ portAvailable: false, appReady: true })
  await new Promise(setImmediate)
  assert.equal(calls.windows, 0)
  assert.equal(calls.quit, 1)
  assert.equal(calls.errors.length, 1)
})

test('a failed backend process stops startup without opening a frontend window', async () => {
  const { calls } = loadMain({ appReady: true, exitOnSpawn: true })
  await new Promise(setImmediate)
  assert.equal(calls.windows, 0)
  assert.equal(calls.quit, 1)
  assert.equal(calls.errors.length, 1)
})
