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
const name=process.env.IMAGE_SYNC_RUN||'run01';if(!/^[\w-]+$/.test(name))throw Error('Invalid run name')
const round=process.env.IMAGE_SYNC_ROUND||'image-sync-01a12efe-round16';if(!/^[\w-]+$/.test(round))throw Error('Invalid evidence round')
const dir=path.join(root,'KnowAll_Mobile/docs/patrol',round,name),assets=path.join(dir,'generated')
await fs.mkdir(dir);await fs.mkdir(assets)
const probe=createServer();probe.listen(0,'127.0.0.1');await once(probe,'listening');const port=probe.address().port;await new Promise(r=>probe.close(r))
const origin=`http://127.0.0.1:${port}`,inputs=new Map(),scenes=[],errors=[],diagnostics=[]
for(const kind of ['mobile','desktop']){
 const entry=kind==='desktop'?path.join(root,'KnowAll_Studio/frontend/tests/image-sync-01a12efe/desktop.jsx'):fileURLToPath(new URL('./mobile.jsx',import.meta.url))
 const built=await build({entryPoints:[entry],outfile:path.join(assets,kind+'.js'),bundle:true,format:'esm',platform:'browser',metafile:true,jsx:'automatic',
 alias:kind==='mobile'?{'react-native':mobileReq.resolve('react-native-web')}:{react:path.dirname(req.resolve('react/package.json')),'react-dom':path.dirname(req.resolve('react-dom/package.json'))},
 define:{'process.env.EXPO_PUBLIC_API_URL':JSON.stringify(origin+'/api/v1'),'process.env.EXPO_PUBLIC_WS_URL':JSON.stringify(origin),'process.env.NODE_ENV':'"production"','import.meta.env':'{}'},logLevel:'warning'})
 await fs.writeFile(path.join(dir,kind+'-metafile.json'),JSON.stringify(built.metafile,null,2))
 for(const name of Object.keys(built.metafile.inputs)){if(name.startsWith('<define:'))continue;const abs=path.resolve(name);inputs.set(abs,{path:path.relative(root,abs).replaceAll('\\','/'),sha256:createHash('sha256').update(await fs.readFile(abs)).digest('hex')})}
}
await fs.mkdir(path.join(assets,'pdfjs-dist/build'),{recursive:true});await fs.copyFile(req.resolve('pdfjs-dist/build/pdf.worker.min.mjs'),path.join(assets,'pdfjs-dist/build/pdf.worker.min.mjs'))
await fs.writeFile(path.join(assets,'mobile.css'),'')
const backendPaths=['KnowAll_Mobile/tests/image-sync-01a12efe/server.py','KnowAll_Mobile/tests/image-sync-01a12efe/launcher.py','KnowAll_Studio/backend/app/api/material_state.py','KnowAll_Studio/backend/app/api/document_preview.py','KnowAll_Studio/backend/app/core/parsing/document_preview.py','KnowAll_Studio/backend/app/api/learning.py','KnowAll_Studio/backend/app/api/documents.py','KnowAll_Studio/backend/app/api/auth.py','KnowAll_Studio/backend/app/core/auth.py','KnowAll_Studio/backend/app/models/material_state.py','KnowAll_Studio/backend/app/models/__init__.py','KnowAll_Studio/backend/app/database.py']
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
const open=async(kind,legacy,documentId='image-a',beforeMount=null)=>{
 const context=await browser.newContext(),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('console',message=>{if(['warning','error'].includes(message.type()))diagnostics.push(message.text().replace(/\beyJ[\w-]+\.[\w-]+\.[\w-]+\b/g,'[token]'))});page.on('response',response=>{if(response.status()>=400)diagnostics.push(response.status()+' '+response.url())});await page.goto(origin+'/__test/page/'+kind);await page.waitForFunction(()=>!!window.client)
 if(legacy)await page.evaluate(({key,value})=>localStorage.setItem(key,value),legacy)
 const auth=await invoke(page,'login',{username:'cross_a',password:'LocalTestOnly_2026!'});await invoke(page,'setAuthToken',auth.access_token);if(beforeMount)await beforeMount(page);await invoke(page,'mountUser',auth.user,auth.access_token,documentId)
 return page
}
const head=p=>invoke(p,'getMaterialState','image-a','image_progress')
const scene=name=>{scenes.push(name);console.log('PASS: '+name)}
const copies=page=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('material-')||k.startsWith('knowall:image-'))))
// The API client baseURL adds /api/v1; fixture control requests use fetch with the signed token.
const fixture=(page,url,method='GET')=>page.evaluate(async({url,method})=>{const token=localStorage.getItem('knowall_token');return (await fetch(url,{method,headers:{Authorization:'Bearer '+token}})).json()},{url,method})
try{
 await start();browser=await chromium.launch({channel:'chrome',headless:true})
 const legacy='{"page":1,"zoom":1.5}',m=await open('mobile',{key:'material-original:cross-a:image-a',value:legacy})
 await expect(m.getByAltText('Original image 1')).toBeVisible({timeout:30000})
 await expect.poll(async()=> (await head(m)).payload?.zoom,{timeout:20000}).toBe(1.5)
 assert.equal(await m.evaluate(()=>localStorage.getItem('material-sync:1:cross-a:image-a:image_progress:legacy-backup')),legacy)
 const d=await open('desktop');await expect(d.getByLabel('图片阅读缩放')).toHaveText('150%',{timeout:20000})
 await expect.poll(()=>d.getByAltText('Original.png').first().evaluate(image=>image.complete&&image.naturalWidth>0)).toBe(true)
 await m.screenshot({path:path.join(dir,'mobile-restored.png')});await d.screenshot({path:path.join(dir,'desktop-restored.png')})
 scene('legacy image first-frame/scale restores in the actual desktop image with immutable mobile backup')
 await d.getByRole('button',{name:'图片放大',exact:true}).click();await expect.poll(async()=> (await head(d)).payload?.zoom).toBe(1.7)
 await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByLabel('mobile zoom')).toHaveText('1.5')
 await expect.poll(()=>m.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:image-a')).sync_zoom)).toBe(1.7)
 assert.equal((await head(m)).payload.zoom,1.7)
 scene('actual desktop 170 percent scale projects on mobile without changing the shared precision')
 await m.getByRole('button',{name:'Zoom two'}).click();await expect.poll(async()=> (await head(m)).payload?.zoom).toBe(2)
 const d2=await open('desktop');await expect(d2.getByLabel('图片阅读缩放')).toHaveText('200%',{timeout:20000})
 scene('actual mobile scale edit reaches a fresh desktop without a default-position overwrite')
 const priorModal=await head(d2)
 await d2.getByRole('button',{name:'放大查看',exact:true}).click();await expect(d2.getByRole('button',{name:'右转',exact:true})).toBeVisible()
 await d2.getByRole('button',{name:'右转',exact:true}).click();await d2.getByRole('button',{name:'横向翻转',exact:true}).click()
 await d2.getByRole('button',{name:'恢复',exact:true}).click();await d2.getByRole('button',{name:'关闭',exact:true}).click()
 assert.deepEqual(await head(d2),priorModal)
 scene('existing full-screen rotate/flip/reset/close tools remain local and usable')
 const badRaw='{"page":1,"zoom":1.5}',beforeFailure=await head(d2)
 const bad=await open('desktop',{key:'knowall:image-position:cross-a:image-a',value:badRaw},'image-a',page=>page.route('**/documents/image-a/raw',route=>route.fulfill({status:200,contentType:'image/png',body:'invalid-image-bytes'})))
 await expect(bad.getByText('图片未能显示，可下载原文件查看。')).toBeVisible({timeout:20000})
 assert.equal((await head(d2)).revision,beforeFailure.revision)
 assert.equal(await bad.evaluate(()=>localStorage.getItem('knowall:image-position:cross-a:image-a')),badRaw)
 scene('an actual failed desktop image load retains its old record and cannot upload a cursor')
 await m.route('**/state/image_progress',route=>route.fulfill({status:503,json:{detail:'offline fixture'}}))
 await m.getByRole('button',{name:'Zoom one'}).click();await expect.poll(()=>m.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:image-a')).zoom)).toBe(1)
 await d2.getByRole('button',{name:'图片放大',exact:true}).click();await expect.poll(async()=> (await head(d2)).payload?.zoom).toBe(2.2)
 await m.unroute('**/state/image_progress');await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByRole('status',{name:'mobile sync'})).toContainText('冲突')
 await fs.writeFile(path.join(dir,'conflict-copies.json'),JSON.stringify(await Promise.all([m,d2,bad].map(copies)),null,2))
 scene('offline image edits and concurrent desktop edits preserve both conflict copies')
 const resolve=(page,choice)=>page.evaluate(choice=>window.probe.resolveSync(choice),choice)
 assert.equal((await resolve(m,'remote')).status,'downloaded');await expect(m.getByLabel('mobile zoom')).toHaveText('2')
 assert.equal(JSON.parse(await m.evaluate(()=>localStorage.getItem('material-original:cross-a:image-a'))).sync_zoom,2.2)
 scene('actual image remote choice preserves exact server scale and archives the local scale')
 await m.route('**/state/image_progress',route=>route.fulfill({status:503,json:{detail:'offline second choice'}}))
 await m.getByRole('button',{name:'Zoom half'}).click();await expect.poll(()=>m.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:image-a')).zoom)).toBe(1.5)
 await d2.getByRole('button',{name:'图片放大',exact:true}).click();await expect.poll(async()=> (await head(d2)).payload?.zoom).toBe(2.4)
 await m.unroute('**/state/image_progress');await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByRole('status',{name:'mobile sync'})).toContainText('冲突')
 const priorChoice=await head(m);assert.equal((await resolve(m,'local')).status,'synced');assert.equal((await head(m)).revision,priorChoice.revision+1);assert.equal((await head(m)).payload.zoom,1.5)
 await fs.writeFile(path.join(dir,'resolved-copies.json'),JSON.stringify(await copies(m),null,2))
 scene('actual image local choice uses fresh revision CAS and retains old remote/local copies')
 const before=await head(d2),traceBefore=await fixture(d2,'/__test/trace');await stop();await start();assert.deepEqual(await head(d2),before)
 scene('image state and operation receipts survive a real backend restart')
 const pending=await (await browser.newContext()).newPage();pending.on('pageerror',e=>errors.push(e.message));await pending.goto(origin+'/__test/page/mobile');await pending.waitForFunction(()=>!!window.client)
 const auth=await invoke(pending,'login',{username:'cross_a',password:'LocalTestOnly_2026!'});await invoke(pending,'setAuthToken',auth.access_token)
 const prepared=await invoke(pending,'getOriginalPreview','image-offline')
 await pending.route('**/documents/image-offline/preview',route=>route.fulfill({status:503,json:{detail:'metadata offline'}}))
 await pending.route('**/documents/image-offline/state/image_progress',route=>route.fulfill({status:503,json:{detail:'state offline'}}))
 await pending.evaluate(()=>localStorage.setItem('material-original:cross-a:image-offline','{"page":1,"zoom":1.5}'))
 await invoke(pending,'mountUser',auth.user,auth.access_token,'image-offline',prepared);await expect(pending.getByAltText('Original image 1')).toBeVisible({timeout:20000})
 await pending.getByRole('button',{name:'Zoom two'}).click();await expect.poll(()=>pending.evaluate(()=>JSON.parse(localStorage.getItem('material-original:cross-a:image-offline')).zoom)).toBe(2)
 await fixture(d2,'/__test/source-change','POST')
 const response=await m.evaluate(async()=>{try{await window.client.getOriginalPage('image-a',1);return 200}catch(e){return e.response?.status}});assert.equal(response,412)
 await m.getByRole('button',{name:'Save original'}).click();await expect(m.getByRole('status',{name:'mobile sync'})).toContainText('图片原件已变化')
 assert.equal((await resolve(m,'local')).status,'conflict')
 scene('changed raw image bytes reject old preview and old displayed-source reanchor')
 await pending.unroute('**/documents/image-offline/preview');await pending.unroute('**/documents/image-offline/state/image_progress')
 await pending.getByRole('button',{name:'Save original'}).click();await expect(pending.getByRole('status',{name:'mobile sync'})).toContainText('图片原件已变化')
 const pendingRaw=await pending.evaluate(()=>localStorage.getItem('material-original:cross-a:image-offline'))
 assert.equal((await resolve(pending,'remote')).status,'conflict');assert.equal(await pending.evaluate(()=>localStorage.getItem('material-original:cross-a:image-offline')),pendingRaw)
 assert.equal((await invoke(d2,'getMaterialState','image-offline','image_progress')).revision,0)
 await fs.writeFile(path.join(dir,'first-offline-source-copy.json'),JSON.stringify(await copies(pending),null,2))
 scene('first offline image keeps its actually displayed source and rejects a nonexistent remote copy')
 const oldRaw=await m.evaluate(()=>localStorage.getItem('material-original:cross-a:image-a'))
 const fresh=await open('mobile',{key:'material-original:cross-a:image-a',value:oldRaw});await expect(fresh.getByAltText('Original image 1')).toBeVisible({timeout:20000})
 await expect(fresh.getByRole('status',{name:'mobile sync'})).toContainText('图片原件已变化')
 const prior=await head(fresh);assert.equal((await resolve(fresh,'local')).status,'synced');const final=await head(fresh)
 assert.equal(final.source_version,prior.current_source_version);assert.equal(final.revision,prior.revision+1);assert.equal(final.stale,false)
 await fs.writeFile(path.join(dir,'reanchored-copies.json'),JSON.stringify(await copies(fresh),null,2))
 await fresh.screenshot({path:path.join(dir,'mobile-reanchored.png')})
 scene('reopened current image explicitly reanchors even an equal cursor while retaining the old source snapshots')
 const foreign=await (await browser.newContext()).newPage();await foreign.goto(origin+'/__test/page/mobile');await foreign.waitForFunction(()=>!!window.client)
 const authB=await invoke(foreign,'login',{username:'cross_b',password:'LocalTestOnly_2026!'});await invoke(foreign,'setAuthToken',authB.access_token)
 const privateStatus=await foreign.evaluate(async()=>{try{await window.client.getMaterialState('image-a','image_progress');return 200}catch(e){return e.response?.status}});assert.equal(privateStatus,404)
 const kindStatus=await m.evaluate(async()=>{try{await window.client.getMaterialState('image-a','pdf_progress');return 200}catch(e){return e.response?.status}});assert.equal(kindStatus,415)
 scene('real JWT ownership and original-type isolation prevent cross-account or PDF-kind access')
 for(const format of ['gif','tiff','apng','webp']){
   const first=await open('desktop',null,'image-'+format)
   await expect.poll(()=>first.getByAltText('Original.png').first().evaluate(image=>image.complete&&image.naturalWidth>0),{timeout:20000}).toBe(true)
   const mime=await first.getByAltText('Original.png').first().evaluate(async image=>(await fetch(image.src)).headers.get('content-type'));assert.equal(mime,'image/png')
   const pixel=await first.getByAltText('Original.png').first().evaluate(image=>{const canvas=document.createElement('canvas');canvas.width=1;canvas.height=1;const context=canvas.getContext('2d');context.drawImage(image,0,0);return [...context.getImageData(0,0,1,1).data]})
   const text=await first.getByAltText('Original.png').first().evaluate(async image=>String.fromCharCode(...new Uint8Array(await (await fetch(image.src)).arrayBuffer())));assert.equal(text.includes('acTL'),false)
   const expected={gif:[255,0,0,255],tiff:[0,128,0,255],apng:[255,255,0,255],webp:[255,255,0,255]}
   assert.deepEqual(pixel,expected[format]);await expect.poll(async()=> (await invoke(first,'getMaterialState','image-'+format,'image_progress')).payload?.page_number).toBe(1)
   scene('actual '+format+' first-frame raster has versioned PNG bytes and the correct visible pixel')
 }
 const finalTrace=await fixture(d2,'/__test/trace');assert.equal(traceBefore.ai_calls+finalTrace.ai_calls,0);assert.deepEqual(errors,[])
 const drift=[];for(const [abs,row]of inputs)if(createHash('sha256').update(await fs.readFile(abs)).digest('hex')!==row.sha256)drift.push(row.path);assert.deepEqual(drift,[])
 await fs.writeFile(path.join(dir,'results.json'),JSON.stringify({scenes,errors,drift,inputs:inputs.size,head:final},null,2))
 await fs.writeFile(path.join(dir,'trace.json'),JSON.stringify([traceBefore,finalTrace],null,2))
 console.log(`PASS ${scenes.length} actual image hook/renderer scenes; first frame/scale only, no native/APK claim`)
}catch(error){
 const pages=browser?browser.contexts().flatMap(c=>c.pages()):[]
 for(let i=0;i<pages.length;i++){await fs.writeFile(path.join(dir,`failure-page-${i}.txt`),await pages[i].locator('body').innerText());await pages[i].screenshot({path:path.join(dir,`failure-page-${i}.png`)})}
 await fs.writeFile(path.join(dir,'failure.json'),JSON.stringify({scenes,errors,diagnostics,message:String(error)},null,2));throw error
}
finally{try{if(browser)await browser.close()}finally{await stop()}}
