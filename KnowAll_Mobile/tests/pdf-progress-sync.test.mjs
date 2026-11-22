import test from 'node:test'
import assert from 'node:assert/strict'
import { SyncSlot } from '../src/materialStateSync.ts'
let codec,carry,localSource
try { ({pdfStateCodec:codec,preservePdfZoom:carry,pdfLocalSource:localSource}=await import('../src/pdfProgressSync.ts')) } catch(e) { if(e.code!=='ERR_MODULE_NOT_FOUND')throw e }
const payload=(page=2,zoom=1.7)=>({page_number:page,zoom,zoom_basis:'fit_width'})
test('desktop legacy integer and mobile JSON retain their exact original bytes before migration',async()=>{
  assert.equal(typeof codec,'function')
  assert.deepEqual(codec('desktop').decode('2'),payload(2,1))
  assert.deepEqual(codec('mobile').decode('{"page":2,"zoom":2}'),payload(2,2))
  for(const raw of ['0','{','{"page":1,"zoom":0}','{"page":true,"zoom":1}'])assert.throws(()=>codec('desktop').decode(raw))
})
test('unsupported mobile scale is projected for display while preserving the shared precise scale',()=>{
  assert.equal(typeof codec,'function')
  const mobile=codec('mobile'),raw=mobile.encode(payload(),null),shown=JSON.parse(raw)
  assert.equal(shown.zoom,1.5);assert.equal(shown.sync_zoom,1.7);assert.deepEqual(mobile.decode(raw),payload())
  assert.deepEqual(mobile.decode(carry('{"page":3,"zoom":1.5}',raw)),payload(3,1.7))
  assert.deepEqual(mobile.decode(carry('{"page":3,"zoom":2}',raw)),payload(3,2))
})
test('downloaded scale precision is preserved without implicit rounding',()=>{
  const value=payload(2,1.7123456789),c=codec('mobile')
  assert.deepEqual(c.decode(c.encode(value,null)),value)
})
const setup=()=>{
  assert.equal(typeof codec,'function');const c=codec('mobile'),data=new Map([['legacy','{"page":2,"zoom":2}']]),writes=[]
  const storage={getItem:async k=>data.get(k)??null,setItem:async(k,v)=>data.set(k,v)}
  let displayed=false,remote={version:1,account_id:'a',document_id:'pdf',kind:'pdf_progress',revision:0,payload:null,source_version:null,current_source_version:'source',current_page_count:3,stale:false}
  const api={get:async()=>structuredClone(remote),put:async req=>{writes.push(req);remote={...remote,revision:remote.revision+1,payload:req.payload,source_version:req.source_version};return structuredClone(remote)}}
  const slot=new SyncSlot({storage,key:'legacy',accountId:'a',documentId:'pdf',kind:'pdf_progress',...c,api,canUpload:()=>displayed})
  return {slot,data,writes,show:()=>{displayed=true},setRemote:v=>{remote={...remote,...v}}}
}
test('existing PDF position is backed up but never uploaded before actual page confirmation',async()=>{
  const s=setup();assert.equal((await s.slot.sync()).status,'pending');assert.equal(s.writes.length,0)
  assert.equal(s.data.get(s.slot.backupKey),'{"page":2,"zoom":2}');s.show()
  assert.equal((await s.slot.sync()).status,'synced');assert.deepEqual(s.writes[0].payload,payload(2,2))
})
test('PDF remote scale pulls safely and changed source preserves both conflict versions',async()=>{
  const s=setup();s.data.delete('legacy');s.setRemote({revision:1,payload:payload(),source_version:'source'})
  assert.equal((await s.slot.sync()).status,'downloaded');assert.equal(JSON.parse(s.data.get('legacy')).zoom,1.5)
  s.setRemote({stale:true,current_source_version:'new-source'})
  assert.equal((await s.slot.sync()).status,'conflict')
  assert.equal(s.writes.length,0);assert.deepEqual(codec('mobile').decode(s.data.get('legacy')),payload())
  assert([...s.data.entries()].some(([k,v])=>k.includes(':conflict:')&&JSON.parse(v).remote.payload.zoom===1.7))
})
test('render confirmation must match the exact candidate payload, not only any displayed page',async()=>{
  const c=codec('desktop'),data=new Map([['legacy','2']]),writes=[]
  const storage={getItem:async k=>data.get(k)??null,setItem:async(k,v)=>data.set(k,v)}
  const remote={version:1,account_id:'a',document_id:'pdf',kind:'pdf_progress',revision:0,payload:null,source_version:null,current_source_version:'source',current_page_count:3,stale:false}
  const slot=new SyncSlot({storage,key:'legacy',accountId:'a',documentId:'pdf',kind:'pdf_progress',...c,
    canUpload:value=>value.page_number===3,
    api:{get:async()=>remote,put:async body=>{writes.push(body);return {...remote,revision:1,payload:body.payload,source_version:'source'}}}})
  assert.equal((await slot.sync()).status,'pending');assert.equal(writes.length,0)
  await slot.saveRaw('{"page":3,"zoom":1}');assert.equal((await slot.sync()).status,'synced')
  assert.equal(writes[0].payload.page_number,3);assert.equal(data.get(slot.backupKey),'2')
})
test('persisted offline source is sent even when a later renderer opens a new version',async()=>{
  const data=new Map([['legacy','{"page":2,"zoom":2,"source_version":"old-source"}']])
  const storage={getItem:async k=>data.get(k)??null,setItem:async(k,v)=>data.set(k,v)}
  const remote={version:1,account_id:'a',document_id:'pdf',kind:'pdf_progress',revision:0,payload:null,source_version:null,current_source_version:'new-source',current_page_count:3,stale:false},writes=[]
  const slot=new SyncSlot({storage,key:'legacy',accountId:'a',documentId:'pdf',kind:'pdf_progress',...codec('desktop'),
    localSourceVersion:raw=>JSON.parse(raw).source_version,canUpload:()=>true,
    api:{get:async()=>remote,put:async body=>{writes.push(body);if(body.source_version!=='new-source'){const error=Error('source');error.current=remote;throw error}return {...remote,revision:1,payload:body.payload,source_version:body.source_version}}}})
  assert.equal((await slot.sync()).status,'conflict');assert.equal(writes.length,0)
  assert.equal(JSON.parse(data.get('legacy')).source_version,'old-source')
})
test('invalid local source marker reports a storage error without a network call or byte replacement',async()=>{
  const raw='{"page":2,"zoom":2,"source_version":false}',data=new Map([['legacy',raw]])
  let calls=0;const slot=new SyncSlot({storage:{getItem:async k=>data.get(k)??null,setItem:async(k,v)=>data.set(k,v)},key:'legacy',accountId:'a',documentId:'pdf',kind:'pdf_progress',...codec('desktop'),localSourceVersion:localSource,
    api:{get:async()=>{calls++;return {version:1,account_id:'a',document_id:'pdf',kind:'pdf_progress',revision:0,payload:null,source_version:null,current_source_version:'new',stale:false}},put:async()=>{throw Error('unexpected')}}})
  assert.equal((await slot.sync()).status,'storage_error');assert.equal(calls,0);assert.equal(data.get('legacy'),raw)
})
test('a corrupt pending source cannot acknowledge an equal tuple from another original version',async()=>{
  const data=new Map([['legacy',JSON.stringify({page:2,zoom:2,source_version:'a'.repeat(64)})]])
  let calls=0;const slot=new SyncSlot({storage:{getItem:async k=>data.get(k)??null,setItem:async(k,v)=>data.set(k,v)},key:'legacy',accountId:'a',documentId:'pdf',kind:'pdf_progress',...codec('desktop'),localSourceVersion:localSource,
    api:{get:async()=>{calls++;return {version:1,account_id:'a',document_id:'pdf',kind:'pdf_progress',revision:1,payload:payload(2,2),source_version:'b'.repeat(64),current_source_version:'b'.repeat(64),stale:false}},put:async()=>{throw Error('unexpected')}}})
  data.set(slot.metaKey,JSON.stringify({version:1,account:'a',document:'pdf',kind:'pdf_progress',base:null,pending:{id:'op',revision:0,payload:payload(2,2),source:false},conflict:null}))
  assert.equal((await slot.sync()).status,'storage_error');assert.equal(calls,0)
})
