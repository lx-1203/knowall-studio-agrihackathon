import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs/promises'
import { createServer } from 'node:net'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { randomBytes, createHash } from 'node:crypto'
import assert from 'node:assert/strict'
const root = fileURLToPath(new URL('../../../',import.meta.url))
const req=createRequire(path.join(root,'KnowAll_Studio/frontend/package.json')), mobileReq=createRequire(path.join(root,'KnowAll_Mobile/package.json'))
const {build}=req('esbuild'),{chromium,expect}=req('@playwright/test')
const name=process.env.STATE_SYNC_RUN||'run01'; if(!/^[\w-]+$/.test(name))throw Error('Invalid run name')
const dir=path.join(root,'KnowAll_Mobile/docs/patrol/state-sync-01a12efe-round13',name),assets=path.join(dir,'generated')
await fs.mkdir(dir);await fs.mkdir(assets)
const probe=createServer();probe.listen(0,'127.0.0.1');await once(probe,'listening');const port=probe.address().port;await new Promise(r=>probe.close(r))
const origin=`http://127.0.0.1:${port}`,inputs=new Map(),scenes=[]
const backendPaths=['KnowAll_Mobile/tests/state-sync-01a12efe/server.py','KnowAll_Studio/backend/app/api/material_state.py','KnowAll_Studio/backend/app/api/learning.py','KnowAll_Studio/backend/app/api/auth.py','KnowAll_Studio/backend/app/core/auth.py','KnowAll_Studio/backend/app/core/learning_workbench.py','KnowAll_Studio/backend/app/models/material_state.py','KnowAll_Studio/backend/app/models/__init__.py','KnowAll_Studio/backend/app/models/user.py','KnowAll_Studio/backend/app/database.py','KnowAll_Studio/backend/app/config.py']
const backendInputs=await Promise.all(backendPaths.map(async name=>({path:name,sha256:createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex')})))
await fs.writeFile(path.join(dir,'backend-inputs-before.json'),JSON.stringify(backendInputs,null,2))
for(const kind of ['mobile','desktop']){
 const input=fileURLToPath(new URL(`./${kind}.jsx`,import.meta.url))
 const built=await build({entryPoints:[input],outfile:path.join(assets,kind+'.js'),bundle:true,format:'esm',platform:'browser',metafile:true,jsx:'automatic',
  alias:kind==='mobile'?{'react-native':mobileReq.resolve('react-native-web')}: {
    'react':path.dirname(req.resolve('react/package.json')), 'react-dom':path.dirname(req.resolve('react-dom/package.json')) },
  define:{'process.env.EXPO_PUBLIC_API_URL':JSON.stringify(origin+'/api/v1'),'process.env.EXPO_PUBLIC_WS_URL':JSON.stringify(origin),'process.env.NODE_ENV':'"production"','import.meta.env':'{}'},logLevel:'warning'})
 await fs.writeFile(path.join(dir,kind+'-metafile.json'),JSON.stringify(built.metafile,null,2))
 for(const inputPath of Object.keys(built.metafile.inputs)){if(inputPath.startsWith('<define:'))continue;const abs=path.resolve(inputPath);inputs.set(abs,{path:path.relative(root,abs).replaceAll('\\','/'),sha256:createHash('sha256').update(await fs.readFile(abs)).digest('hex')})}
}
await fs.writeFile(path.join(dir,'inputs-before.json'),JSON.stringify([...inputs.values()],null,2))
let server,browser;const errors=[],secret=randomBytes(32).toString('hex')
const start=async()=>{
 server=spawn('python',[fileURLToPath(new URL('./server.py',import.meta.url)),'--db',path.join(dir,'isolated.sqlite'),'--assets',assets,'--port',String(port)],{cwd:root,windowsHide:true,env:{...process.env,CROSS_TEST_JWT:secret}})
 let out='';server.stderr.on('data',d=>{out+=d});
 for(let i=0;i<1200;i++){if(server.exitCode!==null)throw Error(out);try{if((await fetch(origin+'/__test/health')).ok)return}catch{} await new Promise(r=>setTimeout(r,150))}throw Error(out||'readiness timeout')
}
const stop=async()=>{if(server&&server.exitCode===null){const p=once(server,'exit');server.kill();await p}}
const invoke=(page,method,...args)=>page.evaluate(({method,args})=>window.client[method](...args),{method,args})
const open=async(context,kind,legacy)=>{
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(origin+'/__test/page/'+kind);await page.waitForFunction(()=>!!window.client)
 if(legacy)await page.evaluate(({key,value})=>localStorage.setItem(key,value),legacy)
 const auth=await invoke(page,'login',{username:'cross_a',password:'LocalTestOnly_2026!'});await invoke(page,'setAuthToken',auth.access_token);await invoke(page,'mountUser',auth.user,auth.access_token)
 return page
}
const scene=name=>{scenes.push(name);console.log('PASS: '+name)}
try{
 await start();browser=await chromium.launch({channel:'chrome',headless:true});const mc=await browser.newContext(),dc=await browser.newContext()
 const m=await open(mc,'mobile',{key:'material-note:cross-a:knowledge-a',value:'旧本机笔记'}),d=await open(dc,'desktop')
 await expect(m.getByRole('textbox',{name:'mobile note'})).toHaveValue('旧本机笔记')
 await expect(d.getByRole('textbox',{name:'我的学习笔记'})).toHaveValue('旧本机笔记',{timeout:15000})
 const backup='material-sync:1:cross-a:knowledge-a:note:legacy-backup'
 assert.equal(await m.evaluate(k=>localStorage.getItem(k),backup),'旧本机笔记');scene('legacy note backup and real hook/desktop component download')
 await d.getByRole('textbox',{name:'我的学习笔记'}).fill('电脑新笔记')
 await expect.poll(async()=> (await invoke(d,'getMaterialState','knowledge-a','note')).payload?.text).toBe('电脑新笔记')
 await m.getByRole('button',{name:'Save note',exact:true}).click();await expect(m.getByRole('textbox',{name:'mobile note'})).toHaveValue('电脑新笔记')
 scene('desktop edit is pulled into actual mobile hook without losing legacy backup')
 const offline=route=>route.fulfill({status:503,json:{detail:'offline test'}})
 await m.route('**/state/note',offline);await m.getByRole('textbox',{name:'mobile note'}).fill('手机离线笔记')
 await expect.poll(()=>m.evaluate(()=>localStorage.getItem('material-note:cross-a:knowledge-a'))).toBe('手机离线笔记')
 await d.getByRole('textbox',{name:'我的学习笔记'}).fill('另一端并发笔记')
 await expect.poll(async()=> (await invoke(d,'getMaterialState','knowledge-a','note')).payload?.text).toBe('另一端并发笔记')
 await m.unroute('**/state/note',offline);await m.getByRole('button',{name:'Save note',exact:true}).click()
 await expect(m.getByRole('status',{name:'mobile sync',exact:true})).toContainText('冲突')
 assert.equal(await m.evaluate(()=>localStorage.getItem('material-note:cross-a:knowledge-a')),'手机离线笔记')
 await fs.writeFile(path.join(dir,'conflict-copies.json'),JSON.stringify(await m.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('material-')))),null,2))
 scene('offline/concurrent notes preserve both versions; no silent overwrite')
 await m.evaluate(()=>window.probe.note.resolveSync('remote'));await expect(m.getByRole('textbox',{name:'mobile note'})).toHaveValue('另一端并发笔记')
 assert(await m.evaluate(()=>Object.keys(localStorage).filter(k=>k.includes(':conflict:')).some(k=>localStorage.getItem(k).includes('手机离线笔记'))))
 scene('explicit remote selection refreshes the hook and archives the local conflict copy')
 await m.getByRole('button',{name:'Read second',exact:true}).click()
 await expect.poll(async()=> (await invoke(m,'getMaterialState','knowledge-a','text_progress')).payload?.chunk_index).toBe(1)
 assert.equal((await invoke(d,'syncPosition')).status,'downloaded')
 assert.equal(await d.evaluate(()=>JSON.parse(localStorage.getItem('knowall:material-position:cross-a:knowledge-a')).chunkIndex),1)
 const m2=await open(await browser.newContext(),'mobile')
 await expect(m2.getByLabel('mobile index')).toHaveText('1',{timeout:15000})
 assert.equal(await m2.evaluate(()=>JSON.parse(localStorage.getItem('material-progress:cross-a:knowledge-a')).fontSize),18)
 assert.equal(await m.evaluate(()=>JSON.parse(localStorage.getItem('material-progress:cross-a:knowledge-a')).fontSize),24)
 scene('text position reaches desktop and fresh mobile hook; device font sizes stay local')
 await invoke(d,'savePosition',0)
 await expect.poll(async()=> (await invoke(d,'getMaterialState','knowledge-a','text_progress')).payload?.chunk_index).toBe(0)
 await m2.reload();await m2.waitForFunction(()=>!!window.client)
 const restored=await invoke(m2,'login',{username:'cross_a',password:'LocalTestOnly_2026!'});await invoke(m2,'setAuthToken',restored.access_token);await invoke(m2,'mountUser',restored.user,restored.access_token)
 await expect(m2.getByLabel('mobile index')).toHaveText('0',{timeout:15000})
 await expect.poll(()=>m2.evaluate(()=>JSON.parse(localStorage.getItem('material-progress:cross-a:knowledge-a')).index)).toBe(0)
 assert.equal(await m2.evaluate(()=>JSON.parse(localStorage.getItem('material-sync:1:cross-a:knowledge-a:text_progress:legacy-backup')).index),1)
 scene('actual desktop position save reaches mobile on reopen and preserves its old position')
 await fs.writeFile(path.join(dir,'local-copies-after.json'),JSON.stringify(await Promise.all([m,d,m2].map(p=>p.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('material-')||k.startsWith('knowall:material-')))))),null,2))
 const noteHead=await invoke(d,'getMaterialState','knowledge-a','note'),positionHead=await invoke(d,'getMaterialState','knowledge-a','text_progress')
 const traceBefore=await d.evaluate(async()=> (await fetch('/__test/trace',{headers:{Authorization:'Bearer '+localStorage.getItem('knowall_token')}})).json())
 assert.equal(traceBefore.ai_calls,0)
 await stop();await start()
 assert.deepEqual(await invoke(d,'getMaterialState','knowledge-a','note'),noteHead)
 assert.deepEqual(await invoke(m,'getMaterialState','knowledge-a','text_progress'),positionHead)
 scene('state revisions and payload survive backend restart')
 assert.deepEqual(errors,[])
 const drift=[];for(const [abs,saved]of inputs)if(createHash('sha256').update(await fs.readFile(abs)).digest('hex')!==saved.sha256)drift.push(saved.path)
 for(const saved of backendInputs)if(createHash('sha256').update(await fs.readFile(path.join(root,saved.path))).digest('hex')!==saved.sha256)drift.push(saved.path)
 assert.deepEqual(drift,[])
 await fs.writeFile(path.join(dir,'results.json'),JSON.stringify({scenes,errors,client_inputs:inputs.size,drift,noteHead,positionHead},null,2))
 const traceAfter=await d.evaluate(async()=> (await fetch('/__test/trace',{headers:{Authorization:'Bearer '+localStorage.getItem('knowall_token')}})).json())
 assert.equal(traceAfter.ai_calls,0);assert.notEqual(traceBefore.pid,traceAfter.pid)
 await fs.writeFile(path.join(dir,'trace.json'),JSON.stringify([traceBefore,traceAfter],null,2))
 console.log(`PASS ${scenes.length} real hook/component synchronization scenes; no native/APK claim.`)
}catch(error){await fs.writeFile(path.join(dir,'failure.json'),JSON.stringify({scenes,errors,message:String(error)},null,2));throw error}
finally{try{if(browser)await browser.close()}finally{await stop()}}
