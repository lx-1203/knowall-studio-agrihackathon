const {_electron,expect}=require('../../frontend/node_modules/playwright/test')
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto')
async function main(){
 const root=path.resolve(__dirname,'..'),out=path.resolve(root,'../docs/open-source-integration'),exe=process.env.KNOWALL_DESKTOP_EXECUTABLE||path.join(root,'release/oss-round11/win-unpacked/KnowAll Studio.exe')
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'knowall-summary-navigation-')),token='summary-fixture-token',requests=[],assets=[]
 const padding=Array.from({length:15},(_,i)=>`正文原文 ${i}：保留来源和内容，不重新生成。`).join('\n\n')
 const content=['# 🔴【核心】 中文 **重点** `code`',padding,'Setext 标题\n============',padding,'## 重复标题 😀',padding,'```md\n# 代码中的伪标题\n```','## 重复标题 😀',padding,'### [三级链接](https://example.test) *强调*',padding,'#### 四级',padding,'##### 五级',padding,'###### 六级',padding].join('\n\n')
 const labels=['🔴【核心】 中文 重点 code','Setext 标题','重复标题 😀','重复标题 😀','三级链接 强调','四级','五级','六级']
 let client,page,report
 const server=http.createServer((req,res)=>{
  const uri=new URL(req.url,'http://localhost').pathname;res.setHeader('Content-Type','application/json')
  const user={id:'summary-user',username:'summary',email:'summary@example.test',is_active:true}
  if(uri==='/health')return res.end('{"status":"ok"}')
  if(uri==='/api/v1/auth/login')return res.end(JSON.stringify({access_token:token,user}))
  if(uri==='/api/v1/auth/me')return res.end(JSON.stringify(user))
  if(uri.startsWith('/api/v1/knowledge/')||uri==='/api/v1/agents/orchestrate'){
   if(req.headers.authorization!==`Bearer ${token}`){res.statusCode=401;return res.end('{}')}
   let body='';req.on('data',b=>body+=b);req.on('end',()=>{
    requests.push({path:uri,authorization:req.headers.authorization,body:body?JSON.parse(body):undefined})
    if(uri.startsWith('/api/v1/knowledge/summary/')){const id=uri.split('/').at(-1);return res.end(JSON.stringify({id,content_md:id==='empty'?'':id==='other'?'# 新目录\n\n新原文':content,document_ids:['source-doc'],node_count:8,level_stats:{core:3}}))}
    if(uri.startsWith('/api/v1/knowledge/coverage/'))return res.end(JSON.stringify({full_coverage_rate:0.5,total_knowledge_points:8,covered_by_questions:4,covered_by_flashcards:2,uncovered_points:[]}))
    if(uri==='/api/v1/agents/orchestrate')return res.end(JSON.stringify({results:{question_bank:{status:'error',error:'暂不可用'},mindmap:{status:'skipped'},study_plan:{status:'error',error:'保留失败原因'},language:{status:'skipped'}}}))
    res.end('[]')
   });return
  }
  res.end('[]')
 })
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
 try{
  const env={...process.env,KNOWALL_DESKTOP_DATA_DIR:data};delete env.KNOWALL_SERVER_URL;delete env.ELECTRON_RUN_AS_NODE
  client=await _electron.launch({executablePath:exe,env,timeout:30000});const settings=await client.firstWindow()
  await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`);const opened=client.waitForEvent('window');await settings.locator('#submit').click();page=await opened
  await page.getByLabel('账号',{exact:true}).fill('summary');await page.getByLabel('密码',{exact:true}).fill('test-password-only');await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('.product-shell').waitFor()
  page.on('request',r=>assets.push(r.url()));await page.setViewportSize({width:1600,height:900});await page.goto(new URL('/summary/nav',page.url()).href)
  const headings=page.locator('.markdown-body').locator('h1,h2,h3,h4,h5,h6'),tree=page.locator('.ant-tree')
  await expect(tree.locator('.ant-tree-title')).toHaveText(labels);await expect(headings).toHaveText(labels)
  const target=headings.filter({hasText:'重复标题 😀'}).nth(1);const before=(await target.boundingBox()).y;assert.ok(before>900)
  await tree.getByText('重复标题 😀',{exact:true}).nth(1).click();await expect(target).toBeFocused();await expect.poll(async()=>(await target.boundingBox()).y).toBeLessThan(900)
  const ids=await headings.evaluateAll(els=>els.map(el=>el.id));assert.equal(new Set(ids).size,8);assert.ok(ids.every(Boolean))
  await page.screenshot({path:path.join(out,'round-11-packaged-summary.png')})
  await page.reload();await expect(tree.locator('.ant-tree-title')).toHaveText(labels);await tree.locator('input').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');await expect(headings.first()).toBeFocused()
  await page.getByRole('tab',{name:/题库 \(\?\)$/}).click();await tree.getByText('六级',{exact:true}).click();await expect(headings.last()).toBeFocused()
  await headings.last().evaluate(el=>{const decoy=document.createElement('h6');decoy.id=el.id;decoy.textContent='其他资料';decoy.tabIndex=-1;document.body.prepend(decoy)})
  await tree.getByText('六级',{exact:true}).click();await expect(headings.last()).toBeFocused()
  await page.getByRole('button',{name:/一键生成全部配套$/}).first().click();await expect(page.getByText('题库生成: 失败',{exact:true})).toBeVisible();assert.deepEqual(requests.find(r=>r.path==='/api/v1/agents/orchestrate').body,{summary_id:'nav',document_id:'source-doc',config:{question_count:30}})
  await page.evaluate(()=>{history.pushState(null,'','/summary/empty');dispatchEvent(new PopStateEvent('popstate'))});await expect(page.getByText('无目录结构',{exact:true})).toBeVisible();await expect(tree).toHaveCount(0)
  await page.evaluate(()=>{history.pushState(null,'','/summary/other');dispatchEvent(new PopStateEvent('popstate'))});await expect(tree.locator('.ant-tree-title')).toHaveText(['新目录']);await tree.getByText('新目录',{exact:true}).click();await expect(headings.first()).toBeFocused()
  await page.evaluate(()=>localStorage.setItem('knowall-theme','dark'));await page.reload();await expect(tree.locator('.ant-tree-title')).toHaveText(['新目录']);await tree.getByText('新目录',{exact:true}).click();await expect(headings.first()).toBeFocused();await page.screenshot({path:path.join(out,'round-11-packaged-summary-dark.png')})
  const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),resources=path.join(path.dirname(exe),'resources');const notices=JSON.parse(await fs.readFile(path.join(out,'round-11-runtime-notices.json'),'utf8'));let checked=0
  for(const item of notices)for(const original of item.original_notices){assert.equal(sha(await fs.readFile(path.join(resources,'frontend/third-party',item.name.replace('@','').replace('/','-'),original.file))),original.sha256);checked++}
  report={at:new Date().toISOString(),executable:exe,app_asar_sha256:sha(await fs.readFile(path.join(resources,'app.asar'))),frontend_index_sha256:sha(await fs.readFile(path.join(resources,'frontend/index.html'))),original_notice_files_checked:checked,requests,assets,passed:true,observations:{native_headings:true,duplicate_second_scroll_focus:true,keyboard:true,tab_return:true,scope:true,empty_changed:true,source_generation_payload:true,dark:true},simulation:'Actual packaged renderer/gateway/native modules; temporary local deterministic account/content HTTP; real cloud workers separately verified'}
 }catch(error){await fs.writeFile(path.join(out,'round-11-packaged-summary-diagnostics.json'),JSON.stringify({url:page?.url(),snapshot:page?await page.locator('body').ariaSnapshot():'',requests,assets},null,2));throw error}
 finally{if(client)await client.close();await new Promise(resolve=>server.close(resolve));assert.equal(path.dirname(data),os.tmpdir());await fs.rm(data,{recursive:true,force:true})}
 report.temporary_data_removed=true;await fs.writeFile(path.join(out,'round-11-packaged-summary.json'),JSON.stringify(report,null,2));console.log('PASS actual packaged summary: native headings/duplicate scroll/focus/keyboard/tab/scope/empty/change/source payload/dark/notices/cleanup')
}
main().catch(error=>{console.error(error);process.exitCode=1})
