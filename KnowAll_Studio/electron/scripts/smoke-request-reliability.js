const {_electron,expect}=require('../../frontend/node_modules/playwright/test')
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto')
async function main(){
 const root=path.resolve(__dirname,'..'),out=path.resolve(root,'../docs/open-source-integration'),exe=process.env.KNOWALL_DESKTOP_EXECUTABLE||path.join(root,'release/oss-round12/win-unpacked/KnowAll Studio.exe')
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'knowall-request-reliability-')),token='reliability-fixture-token',requests=[],assets=[],counts={},aborted=[],timers=new Set()
 let client,page,report,coverageFinished=false
 const server=http.createServer((req,res)=>{
  const uri=new URL(req.url,'http://localhost').pathname;res.setHeader('Content-Type','application/json')
  const user={id:'reliability-user',username:'reliability',email:'reliability@example.test',is_active:true}
  if(uri==='/health')return res.end('{"status":"ok"}')
  if(uri==='/api/v1/auth/login')return res.end(JSON.stringify({access_token:token,user}))
  if(uri==='/api/v1/auth/me')return res.end(JSON.stringify(user))
  let text='';req.on('data',b=>text+=b);req.on('end',()=>{
   const body=text?JSON.parse(text):undefined;requests.push({path:uri,method:req.method,authorization:req.headers.authorization,body});counts[uri]=(counts[uri]||0)+1
   const respond=(value,status=200)=>{res.statusCode=status;res.end(JSON.stringify(value))}
   const later=(value,delay)=>{const timer=setTimeout(()=>{timers.delete(timer);respond(value)},delay);timers.add(timer)}
   if(uri.startsWith('/api/v1/knowledge/')||uri.startsWith('/api/v1/agents/')||uri.startsWith('/api/v1/quiz/')){
    if(req.headers.authorization!==`Bearer ${token}`)return respond({},401)
    if(uri.startsWith('/api/v1/knowledge/summary/')){
     const id=uri.split('/').at(-1),summary={id,content_md:`# 资料 ${id}\n\n云端请求验证原文。`,document_ids:['source-doc'],node_count:8}
     if(id==='recover'&&counts[uri]<3)return respond({detail:'temporary read failure'},503)
     if(id==='throttle'&&counts[uri]===1){res.setHeader('Retry-After','2');return respond({},429)}
     if(id==='slow')return later(summary,5000)
     return respond(summary)
    }
    if(uri==='/api/v1/knowledge/coverage/recover')return later(null,6000)
    if(uri.startsWith('/api/v1/knowledge/coverage/'))return respond(null)
    if(uri==='/api/v1/agents/orchestrate'){
     if(body.summary_id==='generate-a')return later({results:{study_plan:{status:'success',result:{name:'A后台计划',plan_id:'a',ebbinghaus_nodes:[]}}}},5000)
     if(body.summary_id==='preview')return respond({results:{question_bank:{status:'success',result:{total:1}},study_plan:{status:'skipped'},mindmap:{status:'skipped'},language:{status:'skipped'}}})
     return respond({detail:'temporary generation failure'},503)
    }
    if(uri==='/api/v1/quiz/interactive/start')return respond({detail:'uncertain session response'},503)
   }
   respond([])
  })
  if(uri==='/api/v1/knowledge/coverage/recover')res.on('finish',()=>{coverageFinished=true})
 })
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
 try{
  const env={...process.env,KNOWALL_DESKTOP_DATA_DIR:data};delete env.KNOWALL_SERVER_URL;delete env.ELECTRON_RUN_AS_NODE
  client=await _electron.launch({executablePath:exe,env,timeout:30000});const settings=await client.firstWindow()
  await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`);const opened=client.waitForEvent('window');await settings.locator('#submit').click();page=await opened
  await page.getByLabel('账号',{exact:true}).fill('reliability');await page.getByLabel('密码',{exact:true}).fill('temporary-fixture-only');await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('.product-shell').waitFor()
  page.on('request',r=>assets.push(r.url()));page.on('requestfailed',r=>aborted.push({url:r.url(),failure:r.failure()}));await page.setViewportSize({width:1600,height:900})
  const origin=new URL(page.url()).origin,go=async id=>page.goto(`${origin}/summary/${id}`),swap=async id=>page.evaluate(id=>{history.pushState(null,'',`/summary/${id}`);dispatchEvent(new PopStateEvent('popstate'))},id)
  await go('recover');await expect(page.locator('.markdown-body')).toContainText('资料 recover',{timeout:15000});assert.equal(counts['/api/v1/knowledge/summary/recover'],3);assert.equal(coverageFinished,false,'Auxiliary coverage must not hold source loading')
  await page.getByRole('button',{name:/一键生成全部配套$/}).first().click();await expect(page.getByText('生成失败: temporary generation failure',{exact:true})).toBeVisible();assert.equal(counts['/api/v1/agents/orchestrate'],1)
  assert.deepEqual(requests.find(r=>r.path==='/api/v1/agents/orchestrate').body,{summary_id:'recover',document_id:'source-doc',config:{question_count:30}})
  await go('throttle');const began=Date.now();await expect(page.locator('.markdown-body')).toContainText('资料 throttle',{timeout:15000});assert.equal(counts['/api/v1/knowledge/summary/throttle'],2);assert.ok(Date.now()-began>=1800)
  await go('slow');await expect.poll(()=>counts['/api/v1/knowledge/summary/slow']).toBe(1);await swap('b');await expect(page.locator('.markdown-body')).toContainText('资料 b');await expect.poll(()=>aborted.some(r=>r.url.endsWith('/knowledge/summary/slow'))).toBe(true)
  await swap('generate-a');await expect(page.locator('.markdown-body')).toContainText('资料 generate-a');await page.getByRole('button',{name:/一键生成全部配套$/}).first().click();await expect.poll(()=>counts['/api/v1/agents/orchestrate']).toBe(2)
  await swap('b');await expect(page.locator('.markdown-body')).toContainText('资料 b');await expect.poll(()=>aborted.some(r=>r.url.endsWith('/agents/orchestrate'))).toBe(true)
  await page.getByRole('tab',{name:/学习计划$/}).click();await expect(page.getByText('尚未生成学习计划',{exact:true})).toBeVisible();await expect(page.getByText(/服务端.*已取消|A后台计划/)).toBeHidden()
  await swap('preview');await expect(page.locator('.markdown-body')).toContainText('资料 preview');await page.getByRole('button',{name:/一键生成全部配套$/}).first().click();await expect(page.getByText('配套内容生成完成：成功 1 项，跳过 3 项',{exact:true})).toBeVisible();assert.equal(counts['/api/v1/quiz/interactive/start'],1)
  await page.getByRole('tab',{name:/题库 \(\?\)$/}).click();await expect(page.getByText('尚未生成题目',{exact:true})).toBeVisible();await page.screenshot({path:path.join(out,'round-12-packaged-reliability.png')})
  const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),resources=path.join(path.dirname(exe),'resources'),notices=JSON.parse(await fs.readFile(path.join(out,'round-12-runtime-notices.json'),'utf8'));let checked=0
  for(const item of notices)for(const n of item.original_notices){assert.equal(sha(await fs.readFile(path.join(resources,'frontend/third-party',item.name.replace('@','').replace('/','-'),n.file))),n.sha256);checked++}
  assert.ok(requests.filter(r=>r.path.startsWith('/api/v1/knowledge/summary/')||r.path==='/api/v1/agents/orchestrate').every(r=>r.authorization===`Bearer ${token}`))
  report={at:new Date().toISOString(),executable:exe,app_asar_sha256:sha(await fs.readFile(path.join(resources,'app.asar'))),frontend_index_sha256:sha(await fs.readFile(path.join(resources,'frontend/index.html'))),original_notice_files_checked:checked,requests,counts,assets,aborted,passed:true,observations:{read_recovered_after_two_retries:true,write_not_repeated:true,retry_after:true,auxiliary_does_not_block:true,read_and_generation_client_wait_aborted:true,legacy_session_not_retried:true,payload_and_auth_preserved:true},simulation:'Actual packaged renderer/gateway and network HTTP fault fixture. Deterministic temporary account/content; not production cloud or paid AI. Real cloud workers separately verified.'}
 }catch(error){await fs.writeFile(path.join(out,'round-12-packaged-reliability-diagnostics.json'),JSON.stringify({url:page?.url(),snapshot:page?await page.locator('body').ariaSnapshot():'',requests,counts,aborted,assets},null,2));throw error}
 finally{for(const timer of timers)clearTimeout(timer);if(client)await client.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));assert.equal(path.dirname(data),os.tmpdir());await fs.rm(data,{recursive:true,force:true})}
 report.temporary_data_removed=true;await fs.writeFile(path.join(out,'round-12-packaged-reliability.json'),JSON.stringify(report,null,2));console.log('PASS actual packaged read recovery/Retry-After/write once/client abort/legacy session/auth/notices/cleanup')
}
main().catch(error=>{console.error(error);process.exitCode=1})
