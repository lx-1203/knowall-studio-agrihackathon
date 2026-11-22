const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

test('installed and unpackaged cloud clients never invoke the Python launcher', () => {
  for (const isPackaged of [true, false]) {
    let launches = 0
    const context = { __dirname: path.resolve(__dirname, '..'), process: { env: {}, platform: 'win32' }, console, setTimeout,
      require(name) {
        if (name === 'electron') return { app: { isPackaged }, BrowserWindow: class {}, ipcMain: { handle() {} } }
        if (name === './cloud-main') return { launch() { launches++ } }
        if (name === 'path') return path
        if (name === 'child_process') return { spawn() { throw new Error('Python must not spawn') }, execSync() { throw new Error('Python must not run') } }
        if (name === 'http' || name === 'net') return {}
        throw new Error(name)
      } }
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8'), context)
    assert.equal(launches, 1)
  }
})
