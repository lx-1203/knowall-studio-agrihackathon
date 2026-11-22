const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http'), assert = require('node:assert/strict'), crypto = require('node:crypto')
async function main() {
  const root = path.resolve(__dirname, '..'), out = path.resolve(root, '../docs/open-source-integration')
  const exe = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round16/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-account-catalog-'))
  const records = [], held = [], accounts = { A: ['A', 'A-other'], B: ['B'] }
  const doc = id => ({ id, filename: `${id}-private.md`, file_type: 'md', status: 'ready', subject: '', material_kind: 'knowledge', page_count: 1, created_at: new Date(0).toISOString() })
  let page, client, report, holdAccount = 'B', failed = false, indexes = 0
  const server = http.createServer((req, res) => {
    const uri = new URL(req.url, 'http://localhost').pathname;res.setHeader('Content-Type', 'application/json')
    const account = req.headers.authorization?.replace('Bearer ', '').split('-')[0]
    const record = { path: uri, method: req.method, account, bytes: 0, cancelled: false };records.push(record)
    res.on('close', () => { if (!res.writableEnded) record.cancelled = true })
    const chunks = [];req.on('data', b => { chunks.push(b);record.bytes += b.length })
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString(), respond = (v, status=200) => { res.statusCode=status;res.end(JSON.stringify(v)) }
      if (uri === '/health') return respond({ status: 'ok' })
      if (uri === '/api/v1/auth/login') { const id=JSON.parse(body).username;return respond({access_token:`${id}-token`,user:{id,username:id,email:`${id}@example.test`,is_active:true}}) }
      if (uri === '/api/v1/auth/me') return respond({ id:account,username:account,email:`${account}@example.test`,is_active:true })
      if (uri === '/api/v1/documents/') {
        assert.ok(['A','B'].includes(account));record.catalog = true
        if (account === holdAccount) { held.push({ account, res, release:()=>respond(accounts[account].map(doc)) });return }
        if (failed) return respond({ detail:'private-secret-trace' },503)
        return respond(accounts[account].map(doc))
      }
      if (uri === '/api/v1/documents/upload') {
        assert.equal(account,'B');assert.ok(body.includes('name="file"'));assert.ok(body.includes('filename="中文 B 资料.md"'));assert.ok(body.includes('# B账号原始资料'))
        record.original_bytes=true;accounts.B.push('B-uploaded');return respond({document_id:'B-uploaded',filename:'B-uploaded-private.md',total_chunks:1,status:'ready'})
      }
      if (uri === '/api/v1/search/index') { assert.equal(account,'B');indexes++;return respond({indexed:1}) }
      respond([])
    })
  })
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
  try {
    const env={...process.env,KNOWALL_DESKTOP_DATA_DIR:data};delete env.KNOWALL_SERVER_URL;delete env.ELECTRON_RUN_AS_NODE
    client=await _electron.launch({executablePath:exe,env,timeout:30000})
    const settings=await client.firstWindow();await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened=client.waitForEvent('window');await settings.locator('#submit').click();page=await opened;await page.setViewportSize({width:1600,height:950})
    const login=async id=>{await page.getByLabel('账号',{exact:true}).fill(id);await page.getByLabel('密码',{exact:true}).fill('temporary-password');await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.locator('.product-account')).toHaveText(id)}
    await login('A');const origin=new URL(page.url()).origin;await page.goto(origin+'/upload')
    await expect(page.getByText('A-private.md',{exact:true})).toBeVisible()
    await page.getByRole('row').filter({hasText:'A-private.md'}).getByRole('button',{name:/^勾\s*选$/}).click()
    await expect(page.getByRole('button',{name:'返回当前资料'})).toBeVisible()
    await page.getByRole('button',{name:'登出',exact:true}).click();await login('B');await expect.poll(()=>held.some(h=>h.account==='B')).toBe(true)
    await expect(page.getByText('A-private.md',{exact:true})).toBeHidden();await expect(page.getByRole('button',{name:'返回当前资料'})).toBeHidden()
    await expect(page.getByRole('button',{name:'刷新资料',exact:true})).toBeDisabled()
    await page.screenshot({path:path.join(out,'round-16-packaged-B-loading.png'),animations:'disabled'})
    holdAccount=null;held.filter(h=>h.account==='B').forEach(h=>h.release())
    await expect(page.getByText('B-private.md',{exact:true})).toBeVisible()
    holdAccount='A';await page.getByRole('button',{name:'登出',exact:true}).click();await login('A');await expect.poll(()=>held.some(h=>h.account==='A')).toBe(true)
    await page.getByRole('button',{name:'登出',exact:true}).click();await login('B');await expect(page.getByText('B-private.md',{exact:true})).toBeVisible()
    await expect.poll(()=>records.filter(r=>r.catalog&&r.account==='A'&&r.cancelled).length).toBe(1)
    holdAccount=null;held.filter(h=>h.account==='A').forEach(h=>h.release())
    await expect(page.getByText('A-private.md',{exact:true})).toBeHidden();await expect(page.getByText('B-private.md',{exact:true})).toBeVisible()
    failed=true;const before=records.filter(r=>r.catalog&&r.account==='B').length
    await page.getByRole('button',{name:'刷新资料',exact:true}).click();await expect(page.getByRole('alert')).toContainText('资料列表加载失败')
    assert.equal(records.filter(r=>r.catalog&&r.account==='B').length-before,3)
    await expect(page.getByText('暂无资料，请上传你的第一个文档',{exact:true})).toBeHidden();await expect(page.getByText('private-secret-trace')).toBeHidden()
    await page.screenshot({path:path.join(out,'round-16-packaged-catalog-error.png'),animations:'disabled'})
    failed=false;await page.getByRole('button',{name:'重新加载资料',exact:true}).click();await expect(page.getByRole('alert')).toBeHidden()
    const selected=path.join(data,'中文 B 资料.md');await fs.writeFile(selected,'# B账号原始资料\n中文文件。')
    const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:/选择文件$/}).click();await(await chooser).setFiles(selected)
    await expect.poll(()=>indexes).toBe(1);await expect(page.getByText('B-uploaded-private.md',{exact:true})).toBeVisible()
    await expect(page.getByRole('button',{name:'返回当前资料'})).toBeVisible()
    await page.screenshot({path:path.join(out,'round-16-packaged-B-recovered.png'),animations:'disabled'})
    const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),resources=path.join(path.dirname(exe),'resources')
    const notices=JSON.parse(await fs.readFile(path.join(out,'round-16-runtime-notices.json'),'utf8'));let checked=0
    for(const n of notices)for(const f of n.original_notices){assert.equal(sha(await fs.readFile(path.join(resources,'frontend/third-party',n.name.replace('@','').replace('/','-'),f.file))),f.sha256);checked++}
    report={at:new Date().toISOString(),passed:true,executable:exe,app_asar_sha256:sha(await fs.readFile(path.join(resources,'app.asar'))),frontend_index_sha256:sha(await fs.readFile(path.join(resources,'frontend/index.html'))),records,indexes,original_notice_files_checked:checked,observations:{real_logout_login_B_no_A_catalog_or_selected_source:true,real_old_GET_cancelled_on_account_switch:true,late_A_does_not_overwrite_B:true,real503_three_bounded_reads_safe_feedback_manual_recovery:true,current_B_bearer_native_unicode_upload_and_index:true},simulation:'Current actual packaged Electron renderer/gateway/local deterministic HTTP;real temporary account/file/native filechooser event;no OS dialog manual clicks/no production deployment/no paid calls;actual cloud workers verified separately'}
  } catch(error){await fs.writeFile(path.join(out,'round-16-packaged-catalog-diagnostics.json'),JSON.stringify({url:page?.url(),snapshot:page?await page.locator('body').ariaSnapshot():'',records},null,2));throw error}
  finally{if(client)await client.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));assert.equal(path.dirname(data),os.tmpdir());await fs.rm(data,{recursive:true,force:true})}
  report.temporary_data_removed=true;await fs.writeFile(path.join(out,'round-16-packaged-account-catalog.json'),JSON.stringify(report,null,2));console.log('PASS current native account switch/old read cancel/503 bounded recovery/B original file bytes/Bearer/index/original notices/cleanup')
}
main().catch(error=>{console.error(error);process.exitCode=1})
