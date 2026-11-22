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
 const traces=[]
 for(const format of ['apng','webp']){
   const first=await open('desktop',null,'image-'+format)
   await expect.poll(()=>first.getByAltText('Original.png').first().evaluate(image=>image.complete&&image.naturalWidth>0),{timeout:20000}).toBe(true)
   const rendered=await first.getByAltText('Original.png').first().evaluate(async image=>{const response=await fetch(image.src);return {mime:response.headers.get('content-type'),bytes:String.fromCharCode(...new Uint8Array(await response.arrayBuffer()))}})
   assert.equal(rendered.mime,'image/png');assert.equal(rendered.bytes.includes('acTL'),false)
   traces.push(await fixture(first,'/__test/trace'));scene('static first-frame '+format+' preview')
 }
 await fs.writeFile(path.join(dir,'results.json'),JSON.stringify({scenes,errors,inputs:inputs.size},null,2))
}catch(error){
 const pages=browser?browser.contexts().flatMap(c=>c.pages()):[]
 for(let i=0;i<pages.length;i++){await fs.writeFile(path.join(dir,`failure-page-${i}.txt`),await pages[i].locator('body').innerText());await pages[i].screenshot({path:path.join(dir,`failure-page-${i}.png`)})}
 await fs.writeFile(path.join(dir,'failure.json'),JSON.stringify({scenes,errors,diagnostics,message:String(error)},null,2));throw error
}
finally{try{if(browser)await browser.close()}finally{await stop()}}
