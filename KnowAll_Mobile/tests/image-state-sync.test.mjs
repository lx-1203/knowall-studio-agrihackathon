import test from 'node:test'
import assert from 'node:assert/strict'
import {SyncSlot} from '../src/materialStateSync.ts'
import {pdfStateCodec,preservePdfZoom} from '../src/pdfProgressSync.ts'
function setup(extra={}){
  const data=new Map([['legacy','{"page":1,"zoom":1.5}']]),writes=[],codec=pdfStateCodec('mobile')
  const storage={getItem:async k=>data.get(k)??null,setItem:async(k,v)=>{data.set(k,v)}}
  let remote={version:1,account_id:'a',document_id:'image',kind:'image_progress',revision:0,payload:null,source_version:null,current_source_version:'a'.repeat(64),current_page_count:1,stale:false}
  const api={get:async()=>structuredClone(remote),put:async request=>{writes.push(request);remote={...remote,revision:remote.revision+1,payload:request.payload,source_version:request.source_version,stale:false};return structuredClone(remote)}}
  const slot=new SyncSlot({storage,key:'legacy',accountId:'a',documentId:'image',kind:'image_progress',...codec,api,...extra})
  return {data,writes,slot,setRemote:v=>{remote={...remote,...v}}}
}
test('image kind uploads its single first-frame position and retains legacy raw bytes',async()=>{
  const s=setup();assert.equal((await s.slot.sync()).status,'synced');assert.equal(s.writes[0].payload.page_number,1)
  assert.equal(s.data.get(s.slot.backupKey),'{"page":1,"zoom":1.5}');assert(s.slot.metaKey.endsWith(':image_progress'))
})
test('image remote precision pulls while mobile display projection and local navigation retain it',async()=>{
  const s=setup();s.data.delete('legacy');s.setRemote({revision:1,payload:{page_number:1,zoom:1.7123,zoom_basis:'fit_width'},source_version:'a'.repeat(64)})
  assert.equal((await s.slot.sync()).status,'downloaded');const raw=s.data.get('legacy')
  assert.equal(JSON.parse(raw).zoom,1.5);assert.equal(JSON.parse(raw).sync_zoom,1.7123)
  assert.equal(JSON.parse(preservePdfZoom('{"page":1,"zoom":1.5}',raw)).sync_zoom,1.7123)
})
test('image candidates cannot upload before successful actual display',async()=>{
  const s=setup({canUpload:()=>false});assert.equal((await s.slot.sync()).status,'pending');assert.equal(s.writes.length,0)
})
test('image page 2 is invalid and cannot enter cloud state',async()=>{
  const s=setup();s.data.set('legacy','{"page":2,"zoom":1.5}');assert.equal((await s.slot.sync()).status,'storage_error');assert.equal(s.writes.length,0)
})
test('equal image tuples from a different source are conflicts, never acknowledgements',async()=>{
  const s=setup({localSourceVersion:()=> 'a'.repeat(64)});s.setRemote({revision:1,payload:{page_number:1,zoom:1.5,zoom_basis:'fit_width'},source_version:'b'.repeat(64),current_source_version:'b'.repeat(64)})
  assert.equal((await s.slot.sync()).status,'conflict');assert.equal(s.writes.length,0);assert(JSON.parse(s.data.get(s.slot.metaKey)).conflict)
})
test('image explicit reanchor requires actual current-source proof and writes even equal tuples',async()=>{
  let current=false;const s=setup({canUpload:()=>true,canResolveLocal:()=>current})
  s.setRemote({revision:1,payload:{page_number:1,zoom:1.5,zoom_basis:'fit_width'},source_version:'old',current_source_version:'new',stale:true})
  await s.slot.sync();assert.equal((await s.slot.resolve('local')).status,'conflict');assert.equal(s.writes.length,0)
  current=true;assert.equal((await s.slot.resolve('local')).status,'synced');assert.equal(s.writes[0].source_version,'new')
  assert([...s.data.keys()].some(k=>k.includes(':conflict:')))
})
test('a local edit without new render proof retains the previous original source marker',()=>{
  const source='a'.repeat(64),old=JSON.stringify({page:1,zoom:1.5,source_version:source})
  assert.equal(JSON.parse(preservePdfZoom('{"page":1,"zoom":2}',old)).source_version,source)
})
