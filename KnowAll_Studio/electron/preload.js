const { contextBridge } = require('electron')

// File selection uses the native browser input; the renderer only needs metadata.
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  isElectron: true,
})
