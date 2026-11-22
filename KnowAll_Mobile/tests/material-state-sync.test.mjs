import { test } from 'node:test'
import assert from 'node:assert/strict'
let SyncSlot
try { ({ SyncSlot } = await import('../src/materialStateSync.ts')) } catch (e) { if (e.code !== 'ERR_MODULE_NOT_FOUND') throw e }
const setup = () => {
  const data=new Map(), storage={getItem:async k=>data.get(k)??null,setItem:async(k,v)=>{data.set(k,v)}}
  let remote={version:1,account_id:'a',document_id:'doc',kind:'note',revision:0,payload:null,source_version:null,current_source_version:'source',stale:false}
  const writes=[]
  const api={get:async()=>structuredClone(remote),put:async request=>{
    writes.push(request)
    if(request.base_revision!==remote.revision) { const e=Error('conflict'); e.current=structuredClone(remote); throw e }
    remote={...remote,revision:remote.revision+1,payload:request.payload}; return structuredClone(remote)
  }}
  const make=() => { assert.equal(typeof SyncSlot,'function'); return new SyncSlot({storage,key:'legacy',accountId:'a',documentId:'doc',kind:'note',decode:raw=>({text:raw}),encode:value=>value.text,api}) }
  return { data,storage,api,writes,make,setRemote:value=>{remote={...remote,...value}} }
}
test('legacy raw backup, first upload and safe remote pull preserve original bytes',async()=>{
  const s=setup(); s.data.set('legacy','旧笔记'); const slot=s.make()
  assert.equal((await slot.sync()).status,'synced'); assert.equal(s.writes[0].payload.text,'旧笔记')
  assert.equal(s.data.get(slot.backupKey),'旧笔记')
  s.setRemote({revision:2,payload:{text:'电脑新笔记'}})
  assert.equal((await slot.sync()).status,'downloaded'); assert.equal(s.data.get('legacy'),'电脑新笔记')
  assert.equal(s.data.get(slot.backupKey),'旧笔记')
})
test('different initial data conflicts and explicit remote resolution preserves local copy',async()=>{
  const s=setup(); s.data.set('legacy','手机笔记'); s.setRemote({revision:1,payload:{text:'电脑笔记'}})
  const slot=s.make(); assert.equal((await slot.sync()).status,'conflict')
  assert.equal(s.writes.length,0); assert.equal(s.data.get('legacy'),'手机笔记')
  assert.equal((await slot.resolve('remote')).status,'downloaded')
  assert.equal(s.data.get('legacy'),'电脑笔记'); assert([...s.data.values()].some(raw=>raw.includes('手机笔记')))
})
test('offline write is local-first; lost response is confirmed by GET without a duplicate PUT',async()=>{
  const s=setup(), slot=s.make(); await slot.sync()
  await slot.saveRaw('离线编辑'); assert.equal(s.data.get('legacy'),'离线编辑')
  const get=s.api.get; s.api.get=async()=>{throw Error('offline')}
  assert.equal((await slot.sync()).status,'offline'); s.api.get=get
  const put=s.api.put; s.api.put=async request=>{await put(request); throw Error('lost')}
  assert.equal((await slot.sync()).status,'offline'); assert.equal(s.writes.length,1)
  s.api.put=put; assert.equal((await slot.sync()).status,'synced'); assert.equal(s.writes.length,1)
})
test('new local edits during GET cannot be overwritten by late remote response',async()=>{
  const s=setup(),slot=s.make(); await slot.sync()
  let release; s.setRemote({revision:1,payload:{text:'远端'}})
  const get=s.api.get; s.api.get=()=>new Promise(resolve=>{release=async()=>resolve(await get())})
  const operation=slot.sync(); while(!release) await new Promise(r=>setImmediate(r))
  await slot.saveRaw('新本机编辑'); await release(); await operation
  assert.equal(s.data.get('legacy'),'新本机编辑')
})
test('bad metadata, failed backup and foreign response never replace local bytes',async()=>{
  const s=setup(); s.data.set('legacy','保留'); const slot=s.make()
  const set=s.storage.setItem; s.storage.setItem=async(k,v)=>{if(k===slot.backupKey)throw Error('full'); return set(k,v)}
  assert.equal((await slot.sync()).status,'storage_error'); assert.equal(s.writes.length,0)
  s.storage.setItem=set; s.data.set(slot.metaKey,'{')
  assert.equal((await slot.sync()).status,'storage_error'); assert.equal(s.data.get('legacy'),'保留')
  s.data.delete(slot.metaKey); s.setRemote({account_id:'b',revision:1,payload:{text:'别人的'}})
  assert.equal((await slot.sync()).status,'offline'); assert.equal(s.data.get('legacy'),'保留')
})
test('a later edit is rebased only after the earlier own write is confirmed',async()=>{
  const s=setup(),slot=s.make(); await slot.sync(); await slot.saveRaw('第一版')
  const put=s.api.put; let release
  s.api.put=request=>new Promise(resolve=>{release=async()=>resolve(await put(request))})
  const first=slot.sync(); while(!release) await new Promise(r=>setImmediate(r))
  await slot.saveRaw('第二版'); await release(); assert.equal((await first).status,'pending')
  s.api.put=put; assert.equal((await slot.sync()).status,'synced')
  assert.deepEqual(s.writes.map(r=>r.base_revision),[0,1]); assert.equal(s.data.get('legacy'),'第二版')
})
test('repeated conflict checks keep each remote revision without duplicating one revision',async()=>{
  const s=setup(); s.data.set('legacy','本机冲突'); s.setRemote({revision:1,payload:{text:'远端第一版'}})
  const slot=s.make(); await slot.sync(); await slot.sync()
  const copies=()=>[...s.data.entries()].filter(([key])=>key.startsWith(slot.metaKey+':conflict:'))
  assert.equal(copies().length,1)
  s.setRemote({revision:2,payload:{text:'远端第二版'}}); await slot.sync()
  assert.equal(copies().length,2)
  assert(copies().some(([,raw])=>JSON.parse(raw).remote.payload.text==='远端第一版'))
  assert(copies().some(([,raw])=>JSON.parse(raw).remote.payload.text==='远端第二版'))
  assert.equal(s.data.get('legacy'),'本机冲突'); assert.equal(s.writes.length,0)
})
