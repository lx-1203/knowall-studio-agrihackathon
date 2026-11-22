import { getMaterialState, getOriginalPreview, getReadOriginalSource, putMaterialState } from '../api'
import { SyncSlot, type SyncResult } from '../materialStateSync'
import { pdfStateCodec, pdfLocalSource, preservePdfZoom } from '../pdfProgressSync'
import { storage } from './storage'
import { syncMessage } from './materialStateSync'
import { queueMaterialStorage } from '../materialStorageQueue.js'

export const pdfSyncMessage=(result:SyncResult)=>result.status==='conflict' && result.remote?.stale
  ? `${result.remote.kind==='image_progress'?'图片':'PDF'}原件已变化：旧位置与冲突副本已保留，请退出资料后重新打开。` : syncMessage(result)
const originalSessionQueueOwner={}

export function createMobilePdfBridge(accountId: string, documentId: string, canApply: () => boolean, canUpload: (page:number,zoom:number) => boolean) {
  const key = `material-original:${accountId}:${documentId}`
  let descriptor: Awaited<ReturnType<typeof getOriginalPreview>> | null = null
  let displayed: ReturnType<typeof getReadOriginalSource> = undefined
  let remoteSource: string | null = null
  let preparing: Promise<void> | null = null
  const prepare = () => {
    if (descriptor) return Promise.resolve()
    if (!preparing) preparing = getOriginalPreview(documentId,undefined,false).then(meta => {descriptor=meta}).finally(()=>{preparing=null})
    return preparing
  }
  const codec=pdfStateCodec('mobile')
  let selected: SyncSlot | null = null
  const makeSlot = (kind:'pdf_progress'|'image_progress') => new SyncSlot({ storage,key,accountId,documentId,kind,...codec,canApply,localSourceVersion:pdfLocalSource,
    canResolveLocal:remote=>!!displayed?.source_version && remote.current_source_version===displayed.source_version && remote.current_page_count===displayed.page_count,
    encode:(value,previous)=>JSON.stringify({...JSON.parse(codec.encode(value,previous)),...(remoteSource?{source_version:remoteSource}:{})}),
    canUpload:payload=>{
      if(!descriptor?.source_version || !displayed?.source_version || !('page_number' in payload) || displayed.page!==payload.page_number)return false
      const projected=JSON.parse(pdfStateCodec('mobile').encode(payload,null))
      return canUpload(projected.page,projected.zoom)
    },
    api:{get:async()=>{
      await prepare()
      const remote = await getMaterialState(documentId,kind)
      remoteSource=remote.source_version
      const expected=displayed ?? descriptor
      return {...remote, stale:remote.stale || remote.current_source_version !== expected?.source_version || remote.current_page_count !== expected?.page_count}
    },put:async body=>{
      try{return await putMaterialState(documentId,kind,body)}
      catch(error){const e=error as {current?:unknown;response?:{data?:{detail?:{current?:unknown}}}};e.current=e.response?.data?.detail?.current;throw error}
    }} })
  const currentSlot = () => {
    const known=descriptor ?? displayed
    if(!known)throw Error('Original type unavailable')
    return selected ?? (selected=makeSlot(known.file_type==='pdf'?'pdf_progress':'image_progress'))
  }
  const listeners=new Set<(result:SyncResult)=>void>()
  let running:Promise<SyncResult>|null=null,wanted=false
  const sync=():Promise<SyncResult>=>{
    wanted=true;if(running)return running
    running=(async()=>{
      let result:SyncResult={status:'pending'}
      while(wanted){
        wanted=false
        try{await prepare();result=await currentSlot().sync()}
        catch{result={status:'offline'}}
        listeners.forEach(fn=>fn(result))
      }
      return result
    })().finally(()=>{running=null})
    return running
  }
  return {slot:{resolve:async(choice:'local'|'remote'):Promise<SyncResult>=>{
    try{await prepare();return await currentSlot().resolve(choice)}catch{return {status:'offline'}}
  }},sync,bindRendered:(page:number)=>{
    const read=getReadOriginalSource(documentId,page)
    if(read){displayed=read;if(!descriptor)descriptor=read}
  },subscribe:(fn:(result:SyncResult)=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn)}},
    store:{queueOwner:originalSessionQueueOwner,getItem:(k:string)=>storage.getItem(k),setItem:async(k:string,raw:string)=>{
      if(k!==key)return storage.setItem(k,raw)
      const value=JSON.parse(preservePdfZoom(raw,await storage.getItem(k)))
      if(displayed?.source_version)value.source_version=displayed.source_version
      if(descriptor || displayed)await currentSlot().saveRaw(JSON.stringify(value))
      else await queueMaterialStorage(storage,key,async()=>{
        // Saving stays local even when the format metadata request is offline.
        const old=await storage.getItem(key),backup=key+':legacy-backup'
        if(old!==null && await storage.getItem(backup)===null)await storage.setItem(backup,old)
        await storage.setItem(key,JSON.stringify(value))
      })
      void sync()
    }} }
}
