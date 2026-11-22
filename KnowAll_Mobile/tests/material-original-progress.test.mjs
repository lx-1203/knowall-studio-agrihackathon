import test from 'node:test'
import assert from 'node:assert/strict'
const { OriginalProgressSession: Session, originalProgressKey: key } = await import('../src/materialOriginalProgress.ts').catch(() => ({}))
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const memory = () => { const data = new Map(); return { data, getItem: async key => data.get(key) ?? null, setItem: async (key, text) => { data.set(key, text) } } }
const saved = (page=2,zoom=2) => JSON.stringify({page,zoom})

test('original progress retains independent legacy keys and never anonymous storage', () => {
  assert.equal(key('a','pdf'), 'material-original:a:pdf'); assert.equal(key(undefined,'pdf'),null)
})
test('corrupt records are not overwritten by rendered default pages or navigation', async () => {
  for(const raw of ['{','null','{}',saved(0),saved(1,1.2),'{"page":"2","zoom":1}']) {
    const store=memory(); store.data.set('a',raw); const s=new Session(store,'a'); s.activate(3)
    assert.equal(await s.load(),false); s.markRendered(1); s.update({page:2}); s.markRendered(2)
    assert.equal(await s.flush(),false); assert.equal(store.data.get('a'),raw)
  }
})
test('read failure preserves edited page while restoring untouched zoom on retry', async () => {
  let fail=true; const store=memory(); store.data.set('a',saved())
  const s=new Session({...store,getItem:async k=>{if(fail)throw Error('read');return store.getItem(k)}},'a'); s.activate(4)
  await s.load(); s.update({page:3}); s.markRendered(3); await s.flush(); assert.equal(store.data.get('a'),saved())
  fail=false; await s.load(); assert.equal(s.snapshot().page,3); assert.equal(s.snapshot().zoom,2)
  await s.flush(); assert.equal(store.data.get('a'),saved(3))
})
test('slow progress loading does not block rendered pages; zoom-only edits retain stored page', async () => {
  const gate=deferred(); const store=memory(); store.data.set('a',saved())
  const s=new Session({...store,getItem:async k=>{await gate.promise;return store.getItem(k)}},'a'); s.activate(4)
  const loading=s.load(); s.markRendered(1); s.update({zoom:1.5}); assert.equal(await s.flush(),false)
  gate.resolve(); await loading; assert.equal(s.snapshot().page,2); assert.equal(s.snapshot().zoom,1.5)
  assert.equal(await s.flush(),false); assert.equal(store.data.get('a'),saved())
  s.markRendered(2); await s.flush(); assert.equal(store.data.get('a'),saved(2,1.5))
})
test('failed or stale page renders cannot replace the last successfully read page', async () => {
  const store=memory(); const s=new Session(store,'a'); s.activate(4); await s.load(); s.markRendered(1); await s.flush()
  s.update({page:2}); s.markRendered(1); await s.flush(); assert.equal(store.data.get('a'),saved(1,1))
  s.markRendered(2); await s.flush(); assert.equal(store.data.get('a'),saved(2,1))
})
test('a newly displayed page is retained even when the following page fails before its queued save starts', async () => {
  const store=memory(); store.data.set('a',saved(1,1)); const s=new Session(store,'a'); s.activate(4); await s.load(); s.markRendered(1)
  s.update({page:2}); s.markRendered(2); const writing=s.flush(); s.update({page:3})
  await writing; assert.equal(store.data.get('a'),saved(2,1))
  // The page-3 request failed: saving again retains page 2 and never records page 3.
  await s.flush(); assert.equal(store.data.get('a'),saved(2,1))
})
test('navigation during pending save drains only successfully rendered positions in order', async () => {
  const gate=deferred(), started=deferred(); const store=memory(); const writes=[]
  store.setItem=async(k,text)=>{writes.push(text);if(writes.length===1){started.resolve();await gate.promise};store.data.set(k,text)}
  const s=new Session(store,'a'); s.activate(4); await s.load(); s.markRendered(1); const writing=s.flush(); await started.promise
  s.update({page:2,zoom:1.5}); s.markRendered(2); gate.resolve(); await writing
  assert.deepEqual(writes,[saved(1,1),saved(2,1.5)])
})
test('reopened original waits for old whole drain including edits after the first snapshot', async () => {
  const gate=deferred(),started=deferred(); const store=memory(); const set=store.setItem
  store.setItem=async(k,text)=>{started.resolve();await gate.promise;await set(k,text)}
  const old=new Session(store,'a'); old.activate(4); await old.load(); old.markRendered(1); const writing=old.flush(); await started.promise
  const next=new Session(store,'a'); next.activate(4); const reading=next.load()
  old.update({page:3,zoom:2}); old.markRendered(3); gate.resolve(); await writing; await reading
  assert.equal(next.snapshot().page,3); assert.equal(next.snapshot().zoom,2)
})
test('save errors retain disk bytes and no automatic replay; explicit retry saves rendered page', async () => {
  let fail=true,writes=0; const store=memory(); store.data.set('a',saved()); const set=store.setItem
  store.setItem=async(k,text)=>{writes++;if(fail)throw Error('full');await set(k,text)}
  const s=new Session(store,'a'); s.activate(4); await s.load(); s.update({page:3}); s.markRendered(3)
  assert.equal(await s.flush(),false); assert.equal(writes,1); assert.equal(store.data.get('a'),saved())
  fail=false; await s.flush(); assert.equal(store.data.get('a'),saved(3))
})
test('confirmed replacement requires a rendered page, preserves corrupt bytes on failure and then recovers', async () => {
  let fail=true; const store=memory(); store.data.set('a','{'); const set=store.setItem
  store.setItem=async(k,text)=>{if(fail)throw Error('full');await set(k,text)}
  const s=new Session(store,'a'); s.activate(4); await s.load(); s.update({page:3,zoom:1.5})
  assert.equal(await s.replaceWithCurrent(),false); assert.equal(s.snapshot().ready,false)
  s.markRendered(3); assert.equal(await s.replaceWithCurrent(),false); assert.equal(store.data.get('a'),'{')
  fail=false; await s.flush(); assert.equal(store.data.get('a'),saved(3,1.5))
})
test('actual page-count clamping requires re-render and delayed old scope cannot affect a new document', {timeout:3000}, async () => {
  const store=memory(); store.data.set('a',saved(20)); const a=new Session(store,'a'); await a.load(); a.activate(3)
  assert.equal(a.snapshot().page,3); assert.equal(await a.flush(),false); a.markRendered(3); await a.flush()
  const gate=deferred(),started=deferred(); const set=store.setItem
  store.setItem=async(k,text)=>{if(k==='a'){started.resolve();await gate.promise};await set(k,text)}
  a.update({zoom:1}); const writing=a.flush(); await started.promise
  const b=new Session(store,'b'); b.activate(1); await b.load(); b.markRendered(1); await b.flush()
  assert.equal(store.data.get('b'),saved(1,1)); gate.resolve(); await writing; assert.equal(store.data.get('a'),saved(3,1))
})
test('download restores an untouched rendered default but cannot replace a new unrendered navigation',async()=>{
  const store=memory(),s=new Session(store,'a');s.activate(3);await s.load();s.markRendered(1)
  store.data.set('a',saved(2,1.5));assert.equal(await s.reloadSaved(),true)
  assert.equal(s.snapshot().page,2);assert.equal(s.snapshot().renderedPage,0)
  s.update({page:3});store.data.set('a',saved(1,1));assert.equal(await s.reloadSaved(),false)
  assert.equal(s.snapshot().page,3)
})
test('separate bridge facades share the original session whole-drain barrier',async()=>{
  const gate=deferred(),started=deferred(),store=memory(),owner={}
  store.data.set('a',saved(1,1));const set=store.setItem;let first=true
  store.setItem=async(k,text)=>{if(first){first=false;started.resolve();await gate.promise}await set(k,text)}
  const facade=()=>({queueOwner:owner,getItem:store.getItem,setItem:store.setItem})
  const old=new Session(facade(),'a');old.activate(4);await old.load();old.update({page:2});old.markRendered(2)
  const writing=old.flush();await started.promise
  const next=new Session(facade(),'a');next.activate(4);const reading=next.load()
  old.update({page:3});old.markRendered(3);gate.resolve();await writing;await reading
  assert.equal(next.snapshot().page,3)
})
