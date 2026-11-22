const server = document.getElementById('server')
const status = document.getElementById('status')
const submit = document.getElementById('submit')
window.knowallConnection.info().then(info => { server.value = info.serverURL; document.getElementById('version').textContent = ' v' + info.version }).catch(() => {})
document.getElementById('connect').addEventListener('submit', async event => {
  event.preventDefault()
  submit.disabled = true
  status.textContent = '正在验证云服务连接…'
  try {
    const result = await window.knowallConnection.connect(server.value)
    if (!result.ok) status.textContent = result.error
  } catch { status.textContent = '连接失败，请检查地址后重试。' }
  finally { submit.disabled = false }
})
