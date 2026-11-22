const { app, BrowserWindow, Menu, dialog, ipcMain, shell, session } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const { pathToFileURL } = require('node:url')
const { normalizeServerURL, loadConnection, saveConnection, checkServer, startGateway } = require('./cloud-gateway')

let window = null
let gateway = null
let connecting = false
let transitioning = false
const settingsURL = pathToFileURL(path.join(__dirname, 'connection.html')).href
const settingsPreload = path.join(__dirname, 'connection-preload.js')
const configurationDir = () => path.join(app.getPath('userData'), 'knowall')

function createWindow(settings) {
  window = new BrowserWindow({ width: settings ? 680 : 1400, height: settings ? 760 : 900,
    minWidth: settings ? 580 : 1024, minHeight: settings ? 560 : 680,
    title: 'KnowAll Studio · 智识工坊', icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true,
      preload: settings ? settingsPreload : path.join(__dirname, 'preload.js') }, show: false })
  window.once('ready-to-show', () => window?.show())
  window.on('closed', () => { window = null })
  const permitted = settings ? settingsURL : gateway.origin
  window.webContents.on('will-navigate', (event, url) => {
    if (settings ? url !== permitted : !url.startsWith(permitted + '/')) event.preventDefault()
  })
  window.webContents.setWindowOpenHandler(({ url }) => {
    // Only explicit user links to ordinary web pages leave the client.
    if (/^https?:\/\//.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  if (settings) void window.loadFile(path.join(__dirname, 'connection.html'))
  else void window.loadURL(gateway.origin + '/')
}

async function showSettings() {
  if (connecting) return
  transitioning = true
  try {
    window?.destroy()
    if (gateway) { await gateway.close(); gateway = null }
    await session.defaultSession.clearStorageData()
    await session.defaultSession.clearCache()
    createWindow(true)
  } finally { transitioning = false }
}

async function connect(serverURL, previousPort = 0) {
  const normalized = normalizeServerURL(serverURL)
  await checkServer(normalized)
  const frontendDir = app.isPackaged ? path.join(process.resourcesPath, 'frontend') : path.join(__dirname, '..', 'frontend', 'dist')
  try { gateway = await startGateway({ serverURL: normalized, frontendDir, port: previousPort }) } catch (error) {
    if (error.code !== 'EADDRINUSE' || !previousPort) throw error
    gateway = await startGateway({ serverURL: normalized, frontendDir })
  }
  try { saveConnection(configurationDir(), { serverURL: normalized, port: gateway.port }) } catch (error) {
    await gateway.close(); gateway = null; throw error
  }
  transitioning = true
  try { window?.destroy(); createWindow(false) } finally { transitioning = false }
}

function launch() {
  if (process.env.KNOWALL_DESKTOP_DATA_DIR) {
    const directory = process.env.KNOWALL_DESKTOP_DATA_DIR
    if (!path.isAbsolute(directory)) throw new Error('KNOWALL_DESKTOP_DATA_DIR must be an absolute path')
    fs.mkdirSync(directory, { recursive: true })
    app.setPath('userData', directory)
  }
  if (!app.requestSingleInstanceLock()) { app.quit(); return }
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus() } })
  ipcMain.handle('cloud-connection-info', event => {
    if (event.sender.getURL() !== settingsURL) throw new Error('Forbidden')
    return { serverURL: loadConnection(configurationDir())?.serverURL || process.env.KNOWALL_SERVER_URL || '', version: app.getVersion() }
  })
  ipcMain.handle('cloud-connect', async (event, serverURL) => {
    if (event.sender.getURL() !== settingsURL || connecting) return { ok: false, error: '当前连接请求不可用。' }
    connecting = true
    try { await connect(serverURL); return { ok: true } } catch (error) { return { ok: false, error: error.message } } finally { connecting = false }
  })
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
      { label: '服务', submenu: [{ label: '更换云服务…', click: () => { void showSettings() } }, { label: '重新连接', click: () => { void showSettings() } }, { type: 'separator' }, { role: 'quit', label: '退出' }] },
      { role: 'editMenu', label: '编辑' }, { role: 'viewMenu', label: '视图' }, { role: 'windowMenu', label: '窗口' },
    ]))
    const saved = loadConnection(configurationDir())
    const url = process.env.KNOWALL_SERVER_URL || saved?.serverURL
    if (!url) { createWindow(true); return }
    connecting = true
    try {
      if (saved && saved.serverURL !== normalizeServerURL(url)) { await session.defaultSession.clearStorageData(); await session.defaultSession.clearCache() }
      await connect(url, saved?.serverURL === url ? saved.port : 0)
    } catch (error) {
      dialog.showErrorBox('云服务连接失败', error.message)
      createWindow(true)
    } finally { connecting = false }
  }).catch(error => { dialog.showErrorBox('启动失败', error.message); app.quit() })
  app.on('window-all-closed', () => { if (!transitioning && process.platform !== 'darwin') app.quit() })
  app.on('activate', () => { if (!window) createWindow(!gateway) })
  app.on('before-quit', () => { if (gateway) { void gateway.close(); gateway = null } })
}

module.exports = { launch }
