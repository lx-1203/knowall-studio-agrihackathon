const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http'), assert = require('node:assert/strict'), crypto = require('node:crypto')
async function main() {
  const root = path.resolve(__dirname, '..'), out = path.resolve(root, '../docs/open-source-integration')
  const exe = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round15/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-native-file-picker-'))
  const token = 'temporary-native-upload-token', requests = [], aborted = [], timers = new Set(), documents = []
  let client, page, cdp, report, uploads = 0, indexes = 0, imports = 0, urlReady = false, uploadFail = false
  const document = id => ({ id, filename: `${id}.md`, file_type: 'md', status: 'ready', subject: '生物', material_kind: 'knowledge', page_count: 1, total_chunks: 1, created_at: new Date(0).toISOString() })
  const server = http.createServer((req, res) => {
    const uri = new URL(req.url, 'http://localhost').pathname; res.setHeader('Content-Type', 'application/json')
    const user = { id: 'upload-user', username: 'upload-user', email: 'upload@example.test', is_active: true }
    if (uri === '/health') return res.end('{"status":"ok"}')
    if (uri === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (uri === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    const record = { path: uri, method: req.method, authorization: req.headers.authorization, content_type: req.headers['content-type'], bytes: 0 }; requests.push(record)
    const chunks = []; req.on('data', b => { record.bytes += b.length; chunks.push(b) }); req.on('aborted', () => { record.body_aborted = true })
    if (uri === '/api/v1/documents/upload') { record.upload_number = ++uploads }
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString(), respond = (value, status = 200) => { res.statusCode = status; res.end(JSON.stringify(value)) }
      if (uri.startsWith('/api/v1/documents/') || uri.startsWith('/api/v1/search/')) {
        if (req.headers.authorization !== `Bearer ${token}`) return respond({}, 401)
        if (uri === '/api/v1/documents/upload') {
          record.original_name = body.includes('filename="中文 资料 space.md"'); record.contains_original = body.includes('# 原始上传正文\n中文 空格路径。'); record.multipart_file_field = body.includes('name="file"')
          if (uploadFail) return respond({ detail: 'temporary native upload failure' },503)
          const id = `uploaded-${record.upload_number}`
          const timer = setTimeout(() => { timers.delete(timer); documents.push(document(id)); respond({ document_id: id, filename: `${id}.md`, total_chunks: 1, status: 'ready' }) }, 2000); timers.add(timer)
          return
        }
        if (uri === '/api/v1/documents/import-url') {
          imports++; record.body = JSON.parse(body)
          if (!urlReady) return respond({ detail: 'temporary URL import failure' }, 503)
          documents.push(document('url-doc')); return respond({ document_id: 'url-doc', total_chunks: 1 })
        }
        if (uri.endsWith('/organization')) { record.body = JSON.parse(body); return respond(document(uri.split('/').at(-2))) }
        if (uri === '/api/v1/search/index') { indexes++; return respond({ indexed: 1 }) }
        if (uri === '/api/v1/documents/') return respond(documents)
        if (uri.endsWith('/chunks')) return respond([{ id: 'chunk', chunk_index: 0, text: '# 原始上传正文\n从账号资料阅读。', metadata: {} }])
        if (uri.endsWith('/raw')) { res.setHeader('Content-Type', 'text/markdown; charset=utf-8'); return res.end('# 原始上传正文\n从账号资料阅读。') }
        if (/\/documents\/uploaded-\d+$/.test(uri)) return respond(document(uri.split('/').at(-1)))
      }
      respond([])
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...process.env, KNOWALL_DESKTOP_DATA_DIR: data }; delete env.KNOWALL_SERVER_URL; delete env.ELECTRON_RUN_AS_NODE
    client = await _electron.launch({ executablePath: exe, env, timeout: 30000 })
    const settings = await client.firstWindow(); await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened = client.waitForEvent('window'); await settings.locator('#submit').click(); page = await opened
    await page.getByLabel('账号', { exact: true }).fill('upload-user'); await page.getByLabel('密码', { exact: true }).fill('temporary-fixture-only')
    await page.getByRole('button', { name: '登录', exact: true }).click(); await page.locator('.product-shell').waitFor(); await page.setViewportSize({ width: 1600, height: 950 })
    page.on('requestfailed', r => aborted.push({ url:r.url(), failure:r.failure() }))
    const origin = new URL(page.url()).origin, go = route => page.goto(origin + route)
    const selected = path.join(data, '中文 资料 space.md'), large = path.join(data, '中文 大文件.md')
    await fs.writeFile(selected, '# 原始上传正文\n中文 空格路径。')
    const buffer = Buffer.alloc(512*1024,'X');Buffer.from('# 原始上传正文\n').copy(buffer);await fs.writeFile(large,buffer)
    const choose = async (file, keyboard=false) => {
      const pending = page.waitForEvent('filechooser')
      const button = page.getByRole('button',{name:/选择文件$/})
      if(keyboard){await button.focus();await button.press('Enter')}else await button.click()
      const chooser = await pending;assert.equal(chooser.isMultiple(),false);await chooser.setFiles(file);return chooser
    }
    const metadata = await page.evaluate(()=>({keys:Object.keys(window.electronAPI).sort(),platform:window.electronAPI.platform,isElectron:window.electronAPI.isElectron}))
    assert.deepEqual(metadata.keys,['isElectron','platform']);assert.equal(metadata.isElectron,true)
    const isolation = await client.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().map(w=>{const p=w.webContents.getLastWebPreferences();return {contextIsolation:p.contextIsolation,sandbox:p.sandbox,nodeIntegration:p.nodeIntegration,webSecurity:p.webSecurity}}))
    assert.ok(isolation.every(p=>p.contextIsolation&&p.sandbox&&!p.nodeIntegration&&p.webSecurity))
    await go('/upload');await choose([]);assert.equal(uploads,0)
    await choose(selected);await expect.poll(()=>indexes).toBe(1);assert.equal(uploads,1)
    await page.screenshot({path:path.join(out,'round-15-packaged-file-chosen.png'),animations:'disabled'})
    uploadFail=true;await choose(selected);await expect(page.getByRole('alert')).toContainText('上传失败');assert.equal(uploads,2)
    uploadFail=false;await choose(selected);await expect.poll(()=>indexes).toBe(2);assert.equal(uploads,3)
    cdp=await page.context().newCDPSession(page);await cdp.send('Network.enable')
    const throttle=enabled=>cdp.send('Network.emulateNetworkConditions',{offline:false,latency:enabled?20:0,downloadThroughput:enabled?10*1024*1024:-1,uploadThroughput:enabled?64*1024:-1})
    await throttle(true);await choose(large);const progress=page.getByLabel('文件上传进度',{exact:true})
    await expect.poll(async()=>{const n=parseInt(await progress.textContent().catch(()=>''));return n>0&&n<100}).toBe(true)
    await expect.poll(()=>requests.find(r=>r.upload_number===4)?.bytes || 0).toBeGreaterThan(0)
    const percent=parseInt(await progress.textContent());await expect(page.getByRole('button',{name:/选择文件$/})).toBeDisabled()
    await page.screenshot({path:path.join(out,'round-15-packaged-file-progress.png'),animations:'disabled'})
    await page.getByRole('button',{name:'停止当前上传',exact:true}).click();await expect.poll(()=>aborted.filter(r=>r.url.endsWith('/documents/upload')).length).toBe(1)
    assert.equal(uploads,4);assert.equal(indexes,2);await expect(page.getByRole('button',{name:/选择文件$/})).toBeEnabled()
    await throttle(false);await choose(selected,true);await expect.poll(()=>indexes).toBe(3);assert.equal(uploads,5)
    await go('/materials');await page.getByRole('button',{name:/上传资料$/}).first().click();await page.getByLabel('上传资料科目',{exact:true}).fill('生物')
    await throttle(true);let pending=page.waitForEvent('filechooser');await page.getByRole('dialog').locator('.ant-upload-drag').click();await(await pending).setFiles(large)
    await expect.poll(async()=>{const n=parseInt(await page.getByLabel('文件上传进度',{exact:true}).textContent().catch(()=>''));return n>0&&n<100}).toBe(true)
    await expect.poll(()=>requests.find(r=>r.upload_number===6)?.bytes || 0).toBeGreaterThan(0)
    const primaryPercent=parseInt(await page.getByLabel('文件上传进度',{exact:true}).textContent())
    await page.getByRole('button',{name:'停止当前上传',exact:true}).click();await expect.poll(()=>aborted.filter(r=>r.url.endsWith('/documents/upload')).length).toBe(2)
    await throttle(false);pending=page.waitForEvent('filechooser');await page.getByRole('dialog').locator('.ant-upload-drag').click();await(await pending).setFiles(selected)
    await expect(page.getByText('文件已传输，正在等待解析结果。',{exact:true})).toBeVisible()
    await expect(page).toHaveURL(/\/materials\/uploaded-7$/);await expect(page.getByRole('heading',{name:'uploaded-7.md',exact:true})).toBeVisible()
    assert.equal(uploads,7);assert.equal(indexes,3)
    assert.deepEqual(requests.find(r=>r.path.endsWith('/organization')).body,{subject:'生物',material_kind:'knowledge'})
    const completed=requests.filter(r=>r.upload_number&&!r.body_aborted);assert.equal(completed.length,5);assert.ok(completed.every(r=>r.contains_original&&r.original_name&&r.multipart_file_field))
    assert.ok(requests.filter(r=>r.path.startsWith('/api/v1/documents/')||r.path.startsWith('/api/v1/search/')).every(r=>r.authorization===`Bearer ${token}`))
    const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),resources=path.join(path.dirname(exe),'resources')
    const notices=JSON.parse(await fs.readFile(path.join(out,'round-15-runtime-notices.json'),'utf8'));let checked=0
    for(const item of notices)for(const n of item.original_notices){assert.equal(sha(await fs.readFile(path.join(resources,'frontend/third-party',item.name.replace('@','').replace('/','-'),n.file))),n.sha256);checked++}
    const electronRecord=JSON.parse(await fs.readFile(path.join(out,'round-15-fixed-upstream-audit.json'),'utf8')).records.find(r=>r.name==='electron')
    assert.equal(sha(await fs.readFile(path.join(path.dirname(exe),'LICENSE.electron.txt'))),electronRecord.originals.find(n=>n.file==='LICENSE').sha256)
    report={at:new Date().toISOString(),executable:exe,passed:true,app_asar_sha256:sha(await fs.readFile(path.join(resources,'app.asar'))),frontend_index_sha256:sha(await fs.readFile(path.join(resources,'frontend/index.html'))),original_notice_files_checked:checked,metadata,isolation,uploads,indexes,requests,aborted,real_partial_percent:{advanced:percent,primary:primaryPercent},observations:{native_filechooser_mouse_enter_cancel_and_same_file_recovery:true,original_real_unicode_space_filename_bytes:true,single_file_input_and_no_path_RPC:true,unchanged_sandbox_contextIsolation_webSecurity:true,native_real_network_progress_cancel_and_retry:true,account_bearer_post_once_classification_source:true},simulation:'Actual current packaged Electron renderer and gateway/local deterministic HTTP receiver. Real browser filechooser event intercepted through CDP/Playwright, setFiles selects real temporary disk files; OS dialog manual clicks not performed; no native dialog mock in GREEN. Actual network throttling and native byte progress, no synthetic progress. Separate actual cloud workers; no paid calls/external URL fetch.'}
  }catch(error){await fs.writeFile(path.join(out,'round-15-packaged-file-picker-diagnostics.json'),JSON.stringify({url:page?.url(),snapshot:page?await page.locator('body').ariaSnapshot():'',requests,aborted,uploads,indexes},null,2));throw error}
  finally{for(const timer of timers)clearTimeout(timer);if(client)await client.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));assert.equal(path.dirname(data),os.tmpdir());await fs.rm(data,{recursive:true,force:true})}
  report.temporary_data_removed=true;await fs.writeFile(path.join(out,'round-15-packaged-native-file-picker.json'),JSON.stringify(report,null,2))
  console.log('PASS actual packaged native filechooser/mouse/Enter/cancel/Unicode disk file/multipart/Bearer/POSTonce/progress/abort/recovery/metadata/sandbox/original notices/cleanup')
}
main().catch(error=>{console.error(error);process.exitCode=1})
