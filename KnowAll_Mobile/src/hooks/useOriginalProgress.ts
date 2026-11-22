import { useEffect, useMemo, useRef, useState } from 'react'
import { AppState } from 'react-native'
import { createMobilePdfBridge, pdfSyncMessage } from '../services/pdfProgressSync'
import { useAuthStore } from '../stores'
import { OriginalProgressSession, originalProgressKey, type OriginalProgressState } from '../materialOriginalProgress'
const empty: OriginalProgressState = { key: '', page: 1, zoom: 1, renderedPage: 0, ready: false, saving: false, dirty: false, error: '' }
export function useOriginalProgress(accountId: string, documentId: string, count: number) {
  const key = originalProgressKey(accountId, documentId)
  const touched=useRef(false)
  const pair = useMemo(() => {
    if(!key)return null
    touched.current=false
    let session:OriginalProgressSession
    const bridge=createMobilePdfBridge(accountId,documentId,()=>{
      const s=session.snapshot()
      return (!touched.current || (!s.dirty && s.renderedPage===s.page)) && useAuthStore.getState().user?.id===accountId
    },(page,zoom)=>{const s=session.snapshot();return s.ready && s.renderedPage===page && s.page===page && s.zoom===zoom && s.renderedPage>0 && useAuthStore.getState().user?.id===accountId})
    session=new OriginalProgressSession(bridge.store,key);return {session,bridge}
  }, [key])
  const session=pair?.session ?? null
  const [cloud,setCloud]=useState({key:'',message:'',loaded:false})
  const [observed, setObserved] = useState(empty)
  const state = observed.key === key ? observed : empty
  useEffect(() => {
    if (!session) { setObserved(empty); return }
    setObserved(session.snapshot()); const unsubscribe = session.subscribe(setObserved)
    const stop=pair!.bridge.subscribe(async result=>{
      if(result.status==='downloaded')await session.reloadSaved()
      setCloud({key:key!,message:pdfSyncMessage(result),loaded:true})
    })
    void session.load().then(ok=>{if(ok)void pair!.bridge.sync();else setCloud({key:key!,message:'',loaded:true})})
    return () => { unsubscribe();stop(); void session.flush() }
  }, [session])
  useEffect(() => { session?.activate(count) }, [session, count])
  useEffect(() => { if (count && state.ready && cloud.key===key && cloud.loaded) void session?.flush() }, [session, count, state.ready, state.page, state.zoom, state.renderedPage,cloud.key,cloud.loaded])
  useEffect(() => {
    if (!session) return
    const subscription = AppState.addEventListener('change', status => { if (status !== 'active') void session.flush();else void pair!.bridge.sync() })
    return () => subscription.remove()
  }, [session])
  return { ...state,error:state.error || (cloud.key===key?cloud.message:''), update: (change: { page?: number; zoom?: number }) => {touched.current=true;session?.update(change);void pair?.bridge.sync()},
    markRendered: (page: number) => {
      session?.markRendered(page)
      if(session?.snapshot().page===page && session.snapshot().renderedPage===page)pair?.bridge.bindRendered(page)
      if(session?.snapshot().ready)void pair?.bridge.sync()
    },
    reload:async()=>{const ok=await session?.load();if(ok)void pair?.bridge.sync();return ok},
    save:async()=>{const ok=await session?.flush();if(ok)void pair?.bridge.sync();return ok},
    resolveSync:async(choice:'local'|'remote')=>{const result=await pair?.bridge.slot.resolve(choice);if(result){if(result.status==='downloaded')await session?.reloadSaved();setCloud({key:key||'',message:pdfSyncMessage(result),loaded:true})}return result},
    replaceWithCurrent: () => session?.replaceWithCurrent() ?? Promise.resolve(false),
  }
}
