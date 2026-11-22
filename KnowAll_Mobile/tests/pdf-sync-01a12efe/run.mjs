import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import fs from 'node:fs/promises'
import {createServer} from 'node:net'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {randomBytes,createHash} from 'node:crypto'
import assert from 'node:assert/strict'
const root=fileURLToPath(new URL('../../../',import.meta.url)),req=createRequire(path.join(root,'KnowAll_Studio/frontend/package.json')),mobileReq=createRequire(path.join(root,'KnowAll_Mobile/package.json'))
const {build}=req('esbuild'),{chromium,expect}=req('@playwright/test')
const name=process.env.PDF_SYNC_RUN||'run01';if(!/^[\w-]+$/.test(name))throw Error('Invalid run name')
const round=process.env.PDF_SYNC_ROUND||'pdf-sync-01a12efe-round14';if(!/^[\w-]+$/.test(round))throw Error('Invalid evidence round')
const choices=process.env.PDF_SYNC_CONFLICT_CHOICES==='1'
const dir=path.join(root,'KnowAll_Mobile/docs/patrol',round,name),assets=path.join(dir,'generated')
await fs.mkdir(dir);await fs.mkdir(assets)
const probe=createServer();probe.listen(0,'127.0.0.1');await once(probe,'listening');const port=probe.address().port;await new Promise(r=>probe.close(r))
const origin=`http://127.0.0.1:${port}`,inputs=new Map(),scenes=[],errors=[],diagnostics=[]
for(const kind of ['mobile','desktop']){
 const entry=kind==='desktop'?path.join(root,'KnowAll_Studio/frontend/tests/pdf-sync-01a12efe/desktop.jsx'):fileURLToPath(new URL('./mobile.jsx',import.meta.url))
 const built=await build({entryPoints:[entry],outfile:path.join(assets,kind+'.js'),bundle:true,format:'esm',platform:'browser',metafile:true,jsx:'automatic',
 alias:kind==='mobile'?{'react-native':mobileReq.resolve('react-native-web')}:{react:path.dirname(req.resolve('react/package.json')),'react-dom':path.dirname(req.resolve('react-dom/package.json'))},
 define:{'process.env.EXPO_PUBLIC_API_URL':JSON.stringify(origin+'/api/v1'),'process.env.EXPO_PUBLIC_WS_URL':JSON.stringify(origin),'process.env.NODE_ENV':'"production"','import.meta.env':'{}'},logLevel:'warning'})
 await fs.writeFile(path.join(dir,kind+'-metafile.json'),JSON.stringify(built.metafile,null,2))
 for(const name of Object.keys(built.metafile.inputs)){if(name.startsWith('<define:'))continue;const abs=path.resolve(name);inputs.set(abs,{path:path.relative(root,abs).replaceAll('\\','/'),sha256:createHash('sha256').update(await fs.readFile(abs)).digest('hex')})}
}
await fs.mkdir(path.join(assets,'pdfjs-dist/build'),{recursive:true});await fs.copyFile(req.resolve('pdfjs-dist/build/pdf.worker.min.mjs'),path.join(assets,'pdfjs-dist/build/pdf.worker.min.mjs'))
await fs.writeFile(path.join(assets,'mobile.css'),'')
const backendPaths=['KnowAll_Mobile/tests/pdf-sync-01a12efe/server.py','KnowAll_Mobile/tests/pdf-sync-01a12efe/launcher.py','KnowAll_Studio/backend/app/api/material_state.py','KnowAll_Studio/backend/app/api/document_preview.py','KnowAll_Studio/backend/app/core/parsing/document_preview.py','KnowAll_Studio/backend/app/api/learning.py','KnowAll_Studio/backend/app/api/documents.py','KnowAll_Studio/backend/app/api/auth.py','KnowAll_Studio/backend/app/core/auth.py','KnowAll_Studio/backend/app/models/material_state.py','KnowAll_Studio/backend/app/models/__init__.py','KnowAll_Studio/backend/app/database.py']
for(const name of backendPaths){const abs=path.join(root,name);inputs.set(abs,{path:name,sha256:createHash('sha256').update(await fs.readFile(abs)).digest('hex')})}
await fs.writeFile(path.join(dir,'inputs-before.json'),JSON.stringify([...inputs.values()],null,2))
let server,browser;const secret=randomBytes(32).toString('hex'),cleanup=[]
const start=async()=>{
 server=spawn('python',[fileURLToPath(new URL('./launcher.py',import.meta.url)),'--db',path.join(dir,'isolated.sqlite'),'--assets',assets,'--port',String(port)],{cwd:root,windowsHide:true,env:{...process.env,CROSS_TEST_JWT:secret}})
 let output='';server.stderr.on('data',d=>{output+=d})
 for(let i=0;i<1200;i++){if(server.exitCode!==null)throw Error(output);try{if((await fetch(origin+'/__test/health')).ok)return}catch{}await new Promise(r=>setTimeout(r,150))}throw Error(output||'Readiness timeout')
}
const stop=async()=>{if(server&&server.exitCode===null){
 const response=await fetch(origin+'/__test/close-renderer',{method:'POST',headers:{'x-fixture-secret':secret}}),closed=await response.json();assert.equal(closed.closed,true)
 const pid=server.pid,ended=once(server,'exit');server.kill();await ended
 const alive=[];for(const target of [pid,...closed.worker_pids]){try{process.kill(target,0);alive.push(target)}catch{}}
 cleanup.push({api_pid:pid,worker_pids:closed.worker_pids,alive});await fs.writeFile(path.join(dir,'cleanup.json'),JSON.stringify(cleanup,null,2));assert.deepEqual(alive,[])
}}
const invoke=(page,method,...args)=>page.evaluate(({method,args})=>window.client[method](...args),{method,args})
const open=async(kind,legacy)=>{
 const context=await browser.newContext(),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('console',message=>{if(['warning','error'].includes(message.type()))diagnostics.push(message.text().replace(/\beyJ[\w-]+\.[\w-]+\.[\w-]+\b/g,'[token]'))});page.on('response',response=>{if(response.status()>=400)diagnostics.push(response.status()+' '+response.url())});await page.goto(origin+'/__test/page/'+kind);await page.waitForFunction(()=>!!window.client)
 if(legacy)await page.evaluate(({key,value})=>localStorage.setItem(key,value),legacy)
 const auth=await invoke(page,'login',{username:'cross_a',password:'LocalTestOnly_2026!'});await invoke(page,'setAuthToken',auth.access_token);await invoke(page,'mountUser',auth.user,auth.access_token)
 return page
}
const head=p=>invoke(p,'getMaterialState','pdf-a','pdf_progress')
const scene=name=>{scenes.push(name);console.log('PASS: '+name)}
const copies=page=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('material-')||k.startsWith('knowall:pdf-'))))
// The API client baseURL adds /api/v1; fixture control requests use fetch with the signed token.
const fixture=(page,url,method='GET')=>page.evaluate(async({url,method})=>{const token=localStorage.getItem('knowall_token');return (await fetch(url,{method,headers:{Authorization:'Bearer '+token}})).json()},{url,method})
try{
 await start();const worker=await fetch(origin+'/__client/pdfjs-dist/build/pdf.worker.min.mjs');assert.equal(worker.status,200);assert.match(worker.headers.get('content-type'),/javascript/)
 browser=await chromium.launch({channel:'chrome',headless:true})
 const legacy='{"page":2,"zoom":1.5}',m=await open('mobile',{key:'material-original:cross-a:pdf-a',value:legacy})
 await expect(m.getByLabel('mobile page')).toHaveText('2');await expect(m.getByAltText('Original page 2')).toBeVisible({timeout:60000})
 await expect.poll(async()=> (await head(m)).payload?.page_number,{timeout:20000}).toBe(2)
 assert.equal(await m.evaluate(()=>localStorage.getItem('material-sync:1:cross-a:pdf-a:pdf_progress:legacy-backup')),legacy)
 const d=await open('desktop')
 await expect(d.getByRole('spinbutton',{name:'PDF 页码'})).toHaveValue('2',{timeout:20000});await expect(d.locator('.react-pdf__Page[data-page-number="2"] canvas')).toBeVisible()
 await expect.poll(()=>m.getByAltText('Original page 2').evaluate(image=>image.complete&&image.naturalWidth>0)).toBe(true)
 await d.screenshot({path:path.join(dir,'desktop-restored.png')});await m.screenshot({path:path.join(dir,'mobile-restored.png')})
 scene('legacy mobile page/zoom reaches actual desktop PDF renderer with immutable backup')
 await d.getByRole('button',{name:'PDF 下一页'}).click();await expect.poll(async()=> (await head(d)).payload?.page_number).toBe(3)
 await d.getByRole('button',{name:'PDF 放大'}).click();await expect.poll(async()=> (await head(d)).payload?.zoom).toBe(1.7)
 await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByLabel('mobile page')).toHaveText('3')
 await expect(m.getByLabel('mobile zoom')).toHaveText('1.5');await expect(m.getByAltText('Original page 3')).toBeVisible()
 assert.equal((await head(m)).payload.zoom,1.7)
 await m.screenshot({path:path.join(dir,'mobile-projected-scale.png')})
 scene('desktop precise 170 percent reaches mobile projected display without a scale rewrite')
 await m.getByRole('button',{name:'Read first'}).click();await expect.poll(async()=> (await head(m)).payload?.page_number).toBe(1)
 assert.equal((await head(m)).payload.zoom,1.7)
 const d2=await open('desktop');await expect(d2.getByRole('spinbutton',{name:'PDF 页码'})).toHaveValue('1',{timeout:20000})
 scene('mobile navigation preserves precise scale and a fresh desktop restores the shared position')
 const old=await open('desktop',{key:'knowall:pdf-position:cross-a:pdf-a',value:'2'})
 await expect(old.getByRole('status')).toContainText('冲突',{timeout:20000})
 assert.equal(await old.evaluate(()=>localStorage.getItem('material-sync:1:cross-a:pdf-a:pdf_progress:legacy-backup')),'2')
 assert.equal(await old.getByRole('spinbutton',{name:'PDF 页码'}).inputValue(),'2')
 scene('legacy desktop integer conflicts safely instead of overwriting a different server position')
 await m.route('**/state/pdf_progress',route=>route.fulfill({status:503,json:{detail:'offline test'}}))
 await m.getByRole('button',{name:'Read third'}).click();await expect.poll(()=>m.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:pdf-a')).page)).toBe(3)
 await d2.getByRole('button',{name:'PDF 下一页'}).click();await expect.poll(async()=> (await head(d2)).payload?.page_number).toBe(2)
 await m.unroute('**/state/pdf_progress');await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByRole('status',{name:'mobile sync'})).toContainText('冲突')
 await fs.writeFile(path.join(dir,'conflict-copies.json'),JSON.stringify(await Promise.all([m,d2,old].map(copies)),null,2))
 scene('offline and concurrent PDF page/zoom edits retain both conflict copies')
 const before=await head(d2),traceBefore=await fixture(d2,'/__test/trace')
 await stop();await start();assert.deepEqual(await head(d2),before)
 const traceAfter=await fixture(d2,'/__test/trace');assert.notEqual(traceBefore.pid,traceAfter.pid)
 scene('PDF versioned state survives a backend restart')
 if(choices){
   const resolve=(page,choice)=>page.evaluate(choice=>window.probe.resolveSync(choice),choice)
   assert.equal((await resolve(m,'remote')).status,'downloaded')
   await expect(m.getByLabel('mobile page')).toHaveText('2');await expect(m.getByAltText('Original page 2')).toBeVisible()
   assert.equal((await head(m)).revision,before.revision)
   assert(Object.values(await copies(m)).some(raw=>raw.includes('local_raw')&&JSON.parse(raw).local_raw&&JSON.parse(JSON.parse(raw).local_raw).page===3))
   scene('actual mobile remote choice restores the valid server copy and retains the displaced local position')
   await m.route('**/state/pdf_progress',route=>route.fulfill({status:503,json:{detail:'choice offline'}}))
   await m.getByRole('button',{name:'Read third'}).click();await expect.poll(()=>m.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:pdf-a')).page)).toBe(3)
   await d2.getByRole('button',{name:'PDF 下一页'}).click();await expect.poll(async()=> (await head(d2)).payload?.page_number).toBe(3)
   await m.unroute('**/state/pdf_progress');await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByRole('status',{name:'mobile sync'})).toContainText('冲突')
   const previous=await head(d2);assert.equal((await resolve(m,'local')).status,'synced')
   const chosen=await head(m);assert.equal(chosen.revision,previous.revision+1);assert.equal(chosen.payload.zoom,2)
   await fs.writeFile(path.join(dir,'resolved-copies.json'),JSON.stringify(await copies(m),null,2))
   scene('actual mobile local choice archives both sides then commits the rendered cursor by CAS')
 }
 // The renderer got real metadata/page A, but the sync bridge's metadata and state are offline.
 const pending=await (await browser.newContext()).newPage();pending.on('pageerror',e=>errors.push(e.message))
 await pending.goto(origin+'/__test/page/mobile');await pending.waitForFunction(()=>!!window.client)
 const pendingAuth=await invoke(pending,'login',{username:'cross_a',password:'LocalTestOnly_2026!'})
 await invoke(pending,'setAuthToken',pendingAuth.access_token)
 const preparedMeta=await invoke(pending,'getOriginalPreview','pdf-offline')
 await pending.route('**/documents/pdf-offline/preview',route=>route.fulfill({status:503,json:{detail:'metadata offline test'}}))
 await pending.route('**/documents/pdf-offline/state/pdf_progress',route=>route.fulfill({status:503,json:{detail:'state offline test'}}))
 await pending.evaluate(()=>localStorage.setItem('material-original:cross-a:pdf-offline','{"page":2,"zoom":1.5}'))
 await invoke(pending,'mountUser',pendingAuth.user,pendingAuth.access_token,'pdf-offline',preparedMeta)
 await expect(pending.getByAltText('Original page 2')).toBeVisible({timeout:20000});await pending.getByRole('button',{name:'Zoom two'}).click()
 await expect.poll(()=>pending.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:pdf-offline')).zoom),{timeout:20000}).toBe(2)
 await fixture(d2,'/__test/source-change','POST');assert.equal((await head(d2)).stale,true)
 await m.getByRole('button',{name:'Read first'}).click();await expect(m.getByRole('status',{name:'image error'})).toContainText('412')
 await expect(m.getByRole('status',{name:'mobile sync'})).toContainText('PDF原件已变化')
 assert.equal(await m.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:pdf-a')).page),3)
 scene('changed original bytes reject old page images and preserve the last rendered local position')
 await pending.unroute('**/documents/pdf-offline/preview');await pending.unroute('**/documents/pdf-offline/state/pdf_progress')
 await pending.getByRole('button',{name:'Save original'}).click()
 await expect(pending.getByRole('status',{name:'mobile sync'})).toContainText('PDF原件已变化',{timeout:20000})
 assert.equal((await invoke(d2,'getMaterialState','pdf-offline','pdf_progress')).revision,0)
 await fs.writeFile(path.join(dir,'first-offline-source-copy.json'),JSON.stringify(await copies(pending),null,2))
 scene('first unuploaded offline position remains bound to the actually displayed original version')
 if(choices){
   const resolve=(page,choice)=>page.evaluate(choice=>window.probe.resolveSync(choice),choice)
   const missingRaw=await pending.evaluate(()=>localStorage.getItem('material-original:cross-a:pdf-offline'))
   assert.equal((await resolve(pending,'remote')).status,'conflict');assert.equal(await pending.evaluate(()=>localStorage.getItem('material-original:cross-a:pdf-offline')),missingRaw)
   const oldRaw=await m.evaluate(()=>localStorage.getItem('material-original:cross-a:pdf-a'))
   assert.equal((await resolve(m,'local')).status,'conflict');assert.equal(await m.evaluate(()=>localStorage.getItem('material-original:cross-a:pdf-a')),oldRaw)
   scene('actual hook rejects missing remote copy and old rendered source without replacing local bytes')
   const fresh=await open('mobile',{key:'material-original:cross-a:pdf-a',value:oldRaw})
   await expect(fresh.getByAltText('Original page 3')).toBeVisible({timeout:20000});await expect(fresh.getByRole('status',{name:'mobile sync'})).toContainText('PDF原件已变化')
   const prior=await head(fresh);assert.equal((await resolve(fresh,'local')).status,'synced')
   const anchored=await head(fresh);assert.equal(anchored.source_version,prior.current_source_version);assert.equal(anchored.revision,prior.revision+1);assert.equal(anchored.stale,false)
   await fs.writeFile(path.join(dir,'reanchored-copies.json'),JSON.stringify(await copies(fresh),null,2))
   scene('reopened actual current PDF permits explicit local reanchor while preserving old source snapshots')
 }
 const finalTrace=await fixture(d2,'/__test/trace');assert.equal(traceBefore.ai_calls+finalTrace.ai_calls,0)
 assert(traceBefore.requests.some(r=>r.path.endsWith('/image')&&r.versioned));assert(finalTrace.requests.some(r=>r.path.endsWith('/image')&&r.status===412))
 assert.deepEqual(errors,[])
 const drift=[];for(const [abs,row]of inputs)if(createHash('sha256').update(await fs.readFile(abs)).digest('hex')!==row.sha256)drift.push(row.path)
 assert.deepEqual(drift,[])
 await fs.writeFile(path.join(dir,'results.json'),JSON.stringify({scenes,errors,drift,inputs:inputs.size,head:before},null,2))
 await fs.writeFile(path.join(dir,'trace.json'),JSON.stringify([traceBefore,finalTrace],null,2))
 await fs.writeFile(path.join(dir,'local-copies-after.json'),JSON.stringify(await Promise.all([m,d2,old].map(copies)),null,2))
 console.log(`PASS ${scenes.length} actual PDF hook/renderer scenes; no native/APK claim`)
}catch(error){
 const pages=browser?browser.contexts().flatMap(c=>c.pages()):[]
 for(let i=0;i<pages.length;i++){await fs.writeFile(path.join(dir,`failure-page-${i}.txt`),await pages[i].locator('body').innerText());await pages[i].screenshot({path:path.join(dir,`failure-page-${i}.png`)})}
 await fs.writeFile(path.join(dir,'failure.json'),JSON.stringify({scenes,errors,diagnostics,message:String(error)},null,2));throw error
}
finally{try{if(browser)await browser.close()}finally{await stop()}}
