import test from 'node:test'
import assert from 'node:assert/strict'
import { SyncSlot } from '../src/materialStateSync.ts'

function setup(kind='note', extra={}) {
  const raw=kind==='note'?'本机':JSON.stringify({page_number:2,zoom:1.5,zoom_basis:'fit_width'})
  const data=new Map([['legacy',raw]]),writes=[]
  const storage={getItem:async key=>data.get(key)??null,setItem:async(key,value)=>{data.set(key,value)}}
  let remote={version:1,account_id:'a',document_id:'d',kind,revision:1,
    payload:kind==='note'?{text:'电脑'}:{page_number:3,zoom:1,zoom_basis:'fit_width'},
    source_version:'old',current_source_version:'old',current_page_count:3,stale:false}
  const api={get:async()=>structuredClone(remote),put:async body=>{
    writes.push(body)
    if(body.base_revision!==remote.revision){const error=Error('CAS');error.current=structuredClone(remote);throw error}
    remote={...remote,revision:remote.revision+1,payload:body.payload,source_version:body.source_version,stale:false}
    return structuredClone(remote)
  }}
  const slot=new SyncSlot({storage,key:'legacy',accountId:'a',documentId:'d',kind,api,
    decode:value=>kind==='note'?{text:value}:JSON.parse(value),encode:value=>kind==='note'?value.text:JSON.stringify(value),...extra})
  return {slot,data,writes,storage,api,raw,setRemote:value=>{remote={...remote,...value}}}
}
const pausedGet=s=>{
  let release,paused=false;const get=s.api.get
  s.api.get=()=>{if(paused)return get();paused=true;return new Promise(resolve=>{release=async()=>resolve(await get())})}
  return {wait:async()=>{while(!release)await new Promise(r=>setImmediate(r))},release:()=>release()}
}
const copies=s=>[...s.data.entries()].filter(([key])=>key.startsWith(s.slot.metaKey+':conflict:')).map(([,raw])=>JSON.parse(raw))

test('missing server copy cannot report downloaded or clear the conflict',async()=>{
  const s=setup();await s.slot.sync();s.setRemote({revision:0,payload:null,source_version:null})
  assert.equal((await s.slot.resolve('remote')).status,'conflict')
  assert.equal(s.data.get('legacy'),s.raw);assert(JSON.parse(s.data.get(s.slot.metaKey)).conflict)
  assert.equal(s.writes.length,0);assert(copies(s).some(c=>c.remote.payload===null))
})
test('new edits during a remote choice refresh are preserved with their pending operation',async()=>{
  const s=setup();await s.slot.sync();const pause=pausedGet(s),operation=s.slot.resolve('remote')
  await pause.wait();await s.slot.saveRaw('刷新时的新编辑');const journal=s.data.get(s.slot.metaKey)
  await pause.release();assert.equal((await operation).status,'pending')
  assert.equal(s.data.get('legacy'),'刷新时的新编辑');assert.equal(s.data.get(s.slot.metaKey),journal);assert.equal(s.writes.length,0)
})
test('a local choice never silently includes an edit made after the click',async()=>{
  const s=setup();await s.slot.sync();const pause=pausedGet(s),operation=s.slot.resolve('local')
  await pause.wait();await s.slot.saveRaw('点击后的编辑');await pause.release()
  assert.equal((await operation).status,'pending');assert.equal(s.writes.length,0);assert(JSON.parse(s.data.get(s.slot.metaKey)).conflict)
})
test('opposite repeated choices do not run a second fetch and background sync shares the choice',async()=>{
  const s=setup();await s.slot.sync();const pause=pausedGet(s),first=s.slot.resolve('remote');await pause.wait()
  let gets=0;const get=s.api.get;s.api.get=async()=>{gets++;return get()}
  const second=await s.slot.resolve('local');const sync=s.slot.sync();await pause.release()
  assert.equal(second.status,'pending');assert.equal((await first).status,'downloaded');assert.equal((await sync).status,'downloaded')
  assert.equal(gets,0);assert.equal(s.writes.length,0);assert.equal(s.data.get('legacy'),'电脑')
})
test('PDF local choice retains a conflict until the exact candidate has rendered',async()=>{
  const s=setup('pdf_progress',{canUpload:()=>false,canResolveLocal:()=>true});await s.slot.sync()
  const journal=s.data.get(s.slot.metaKey)
  assert.equal((await s.slot.resolve('local')).status,'conflict');assert.equal(s.data.get(s.slot.metaKey),journal);assert.equal(s.writes.length,0)
})
test('PDF local choice cannot rebind an old displayed source to the current source',async()=>{
  const s=setup('pdf_progress',{canUpload:()=>true,canResolveLocal:()=>false});await s.slot.sync()
  s.setRemote({current_source_version:'new',stale:true})
  assert.equal((await s.slot.resolve('local')).status,'conflict');assert.equal(s.writes.length,0)
  assert.equal(s.data.get('legacy'),s.raw);assert.equal(JSON.parse(s.data.get(s.slot.metaKey)).pending.source,'old')
})
test('PDF local reanchor with actual new source and page count archives old copies before ordinary CAS',async()=>{
  const s=setup('pdf_progress',{canUpload:()=>true,canResolveLocal:r=>r.current_source_version==='new'&&r.current_page_count===3});await s.slot.sync()
  s.setRemote({current_source_version:'new',stale:true})
  assert.equal((await s.slot.resolve('local')).status,'synced');assert.equal(s.writes.length,1)
  assert.equal(s.writes[0].source_version,'new');assert.equal(s.writes[0].base_revision,1)
  assert(copies(s).some(c=>c.local_raw===s.raw&&c.remote.current_source_version==='new'));assert.equal(s.data.get(s.slot.backupKey),s.raw)
})
test('valid local and remote choices retain both snapshots, including an intentionally empty note',async()=>{
  for(const choice of ['local','remote']) {
    const s=setup();await s.slot.sync();s.setRemote({revision:2,payload:{text:''}})
    assert.equal((await s.slot.resolve(choice)).status,choice==='local'?'synced':'downloaded')
    assert.equal(s.data.get('legacy'),choice==='local'?s.raw:'');assert.equal(s.data.get(s.slot.backupKey),s.raw)
    assert(copies(s).some(c=>c.local_raw===s.raw&&c.remote.revision===2&&c.remote.payload.text===''))
    if(choice==='local')assert.equal(s.writes[0].base_revision,2)
  }
})
test('failed archive or stale remote choice cannot replace local bytes or clear the journal',async()=>{
  for(const failure of ['archive','stale']) {
    const s=setup();await s.slot.sync();const journal=s.data.get(s.slot.metaKey)
    if(failure==='stale')s.setRemote({stale:true})
    else {const set=s.storage.setItem;s.storage.setItem=async(k,v)=>{if(k.includes(':conflict:'))throw Error('full');return set(k,v)}}
    assert.equal((await s.slot.resolve('remote')).status,failure==='stale'?'conflict':'storage_error')
    assert.equal(s.data.get('legacy'),s.raw);assert(JSON.parse(s.data.get(s.slot.metaKey)).conflict);assert.equal(s.writes.length,0)
    if(failure==='archive')assert.equal(s.data.get(s.slot.metaKey),journal)
  }
})
test('corrupt local journal stops resolution before any network or replacement',async()=>{
  const s=setup();s.data.set(s.slot.metaKey,'{');let gets=0;s.api.get=async()=>{gets++;throw Error('must not call')}
  assert.equal((await s.slot.resolve('remote')).status,'storage_error');assert.equal(gets,0);assert.equal(s.data.get('legacy'),s.raw)
})
test('an unsaved editor change during archive IO prevents the final remote replacement',async()=>{
  let applicable=true;const s=setup('note',{canApply:()=>applicable});await s.slot.sync()
  const journal=s.data.get(s.slot.metaKey),set=s.storage.setItem
  s.storage.setItem=async(k,v)=>{await set(k,v);if(k.includes(':conflict:'))applicable=false}
  assert.equal((await s.slot.resolve('remote')).status,'pending');assert.equal(s.data.get('legacy'),s.raw);assert.equal(s.data.get(s.slot.metaKey),journal)
})
test('a server write racing the chosen local upload is still a conflict, never a forced overwrite',async()=>{
  const s=setup();await s.slot.sync();const put=s.api.put
  s.api.put=async body=>{s.setRemote({revision:8,payload:{text:'并发新服务器笔记'}});return put(body)}
  assert.equal((await s.slot.resolve('local')).status,'conflict');assert.equal(s.writes.length,1);assert.equal(s.writes[0].base_revision,1)
  assert.equal(s.data.get('legacy'),s.raw);assert(copies(s).some(c=>c.remote.revision===8))
})
test('text cursor remote resolution retains local font settings and archives the old raw record',async()=>{
  const s=setup('text_progress',{decode:raw=>({chunk_index:JSON.parse(raw).index,chunk_id:null}),
    encode:(value,previous)=>JSON.stringify({index:value.chunk_index,fontSize:JSON.parse(previous).fontSize})})
  const old='{"index":8,"fontSize":24}';s.data.set('legacy',old);s.setRemote({payload:{chunk_index:3,chunk_id:'chunk3'}})
  assert.equal((await s.slot.sync()).status,'conflict');assert.equal((await s.slot.resolve('remote')).status,'downloaded')
  assert.deepEqual(JSON.parse(s.data.get('legacy')),{index:3,fontSize:24});assert.equal(s.data.get(s.slot.backupKey),old);assert(copies(s).some(c=>c.local_raw===old))
})
test('PDF local resolution without reader proof is denied even if the payload looks valid',async()=>{
  const s=setup('pdf_progress',{canUpload:()=>true});await s.slot.sync()
  assert.equal((await s.slot.resolve('local')).status,'conflict');assert.equal(s.writes.length,0);assert.equal(s.data.get('legacy'),s.raw)
})
test('identical PDF tuple across different source versions must write the explicit reanchor, not just acknowledge',async()=>{
  const s=setup('pdf_progress',{canUpload:()=>true,canResolveLocal:()=>true})
  s.setRemote({payload:JSON.parse(s.raw),current_source_version:'new',stale:true});await s.slot.sync()
  assert.equal((await s.slot.resolve('local')).status,'synced');assert.equal(s.writes.length,1);assert.equal(s.writes[0].source_version,'new')
})
test('edits while a choice waits for an earlier sync are not silently selected or replaced',async()=>{
  const s=setup();await s.slot.sync();const pause=pausedGet(s),sync=s.slot.sync();await pause.wait()
  const choice=s.slot.resolve('remote');await new Promise(r=>setImmediate(r));await s.slot.saveRaw('等待旧同步期间编辑')
  await pause.release();await sync;assert.equal((await choice).status,'pending');assert.equal(s.data.get('legacy'),'等待旧同步期间编辑');assert.equal(s.writes.length,0)
})
