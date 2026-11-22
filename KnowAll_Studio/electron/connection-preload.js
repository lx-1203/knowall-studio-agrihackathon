const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('knowallConnection', {
  info: () => ipcRenderer.invoke('cloud-connection-info'),
  connect: serverURL => ipcRenderer.invoke('cloud-connect', serverURL),
})
