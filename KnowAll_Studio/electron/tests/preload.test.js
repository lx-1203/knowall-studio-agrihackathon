const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')

for (const platform of ['win32', 'darwin', 'linux']) {
  test(`renderer bridge on ${platform} contains metadata and no filesystem path RPC`, () => {
    let name, bridge
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.js'), 'utf8'), {
      process: { platform },
      require: module => {
        assert.equal(module, 'electron')
        return { contextBridge: { exposeInMainWorld: (key, api) => { name = key; bridge = api } }, ipcRenderer: { invoke: () => { throw Error('File path IPC must not be invoked') } } }
      },
    })
    assert.equal(name, 'electronAPI')
    assert.deepEqual(Object.keys(bridge).sort(), ['isElectron', 'platform'])
    assert.equal(bridge.isElectron, true); assert.equal(bridge.platform, platform)
  })
}
