import { useEffect, useMemo, useRef, useState } from 'react'
import { useAuthStore } from '../stores'
import { getMaterialState, putMaterialState } from '../api'
import { materialSyncMessage } from '../materialStateSync'
import { SyncSlot, type SyncResult } from '../../../../KnowAll_Mobile/src/materialStateSync'
import { pdfStateCodec, pdfLocalSource } from '../../../../KnowAll_Mobile/src/pdfProgressSync'
const storage={getItem:async(k:string)=>localStorage.getItem(k),setItem:async(k:string,v:string)=>{localStorage.setItem(k,v)}}
const codec=pdfStateCodec('desktop')
export function usePdfProgress(userId:string,documentId:string,fileUrl:string,kind:'pdf_progress'|'image_progress'='pdf_progress') {
  const label=kind==='image_progress'?'图片':'PDF'
  const key=`knowall:${kind==='image_progress'?'image':'pdf'}-position:${userId}:${documentId}`
  const [position,setPosition]=useState({page:1,zoom:1}),[error,setError]=useState(''),[loaded,setLoaded]=useState(false)
  const view=useRef({page:1,zoom:1,count:0,renderedPage:0,renderedZoom:0,touched:false,fault:false})
  const source=useRef('')
  const currentScope=useRef(key);currentScope.current=key
  const slot=useMemo(()=>new SyncSlot({storage,key,accountId:userId,documentId,kind,...codec,localSourceVersion:pdfLocalSource,
    canResolveLocal:remote=>!!source.current && remote.current_source_version===source.current && remote.current_page_count===view.current.count,
    encode:(value,previous)=>JSON.stringify({...JSON.parse(codec.encode(value,previous)),...(source.current?{source_version:source.current}:{})}),
    canApply:()=>{
      if(currentScope.current!==key || useAuthStore.getState().user?.id!==userId)return false
      if(!view.current.touched)return true
      try{const saved=codec.decode(localStorage.getItem(key)||'');return !view.current.fault && saved.page_number===view.current.page && saved.zoom===view.current.zoom && view.current.renderedPage===view.current.page && view.current.renderedZoom===view.current.zoom}catch{return false}
    },
    canUpload:payload=>!!source.current && 'page_number' in payload && payload.page_number===view.current.page && payload.zoom===view.current.zoom && view.current.count>0 && view.current.renderedPage===view.current.page && view.current.renderedZoom===view.current.zoom && useAuthStore.getState().user?.id===userId,
    api:{get:async()=>{
      const state=await getMaterialState(documentId,kind)
      return {...state,stale:state.stale || state.current_source_version!==source.current || (view.current.count>0 && state.current_page_count!==view.current.count)}
    },put:async body=>{
      try{return await putMaterialState(documentId,kind,body)}
      catch(error){const e=error as {current?:unknown;response?:{data?:{detail?:{current?:unknown}}}};e.current=e.response?.data?.detail?.current;throw error}
    }} }),[key,fileUrl])
  const restore=()=>{
    const raw=localStorage.getItem(key)
    if(raw!==null)pdfLocalSource(raw)
    const value=raw===null?{page_number:1,zoom:1}:codec.decode(raw)
    view.current.page=value.page_number;view.current.zoom=value.zoom
    view.current.renderedPage=0;view.current.renderedZoom=0
    setPosition({page:value.page_number,zoom:value.zoom})
  }
  const sync=async()=>{
    const result=await slot.sync()
    if(currentScope.current!==key)return result
    if(result.status==='storage_error')view.current.fault=true
    if(result.status==='downloaded')restore()
    setError(materialSyncMessage(result));return result
  }
  useEffect(()=>{
    let live=true;source.current='';setLoaded(false);setError('')
    view.current={page:1,zoom:1,count:0,renderedPage:0,renderedZoom:0,touched:false,fault:false}
    try{restore()}catch{view.current.fault=true;setError(`${label}本机进度读取失败，旧记录未覆盖。`)}
    if(userId==='local'){setLoaded(true);return()=>{live=false}}
    // FilePreview supplies an authorized blob URL: hash those exact displayed bytes.
    void fetch(fileUrl).then(async response=>{
      if(!response.ok)throw Error('Original source unavailable')
      const bytes=await response.arrayBuffer(),hash=await crypto.subtle.digest('SHA-256',bytes)
      if(!live)return
      source.current=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('')
      const result=await slot.sync();if(!live)return
      if(result.status==='storage_error')view.current.fault=true
      if(result.status==='downloaded')restore()
      setError(materialSyncMessage(result));setLoaded(true)
    }).catch(()=>{if(live){setError(`${label}来源暂未确认，原本机记录保留。`);setLoaded(true)}})
    return()=>{live=false}
  },[slot])
  const save=async()=>{
    const state=view.current
    if(!loaded || state.fault || !state.count || state.renderedPage!==state.page || state.renderedZoom!==state.zoom)return
    try{
      const raw=JSON.stringify({page:state.page,zoom:state.zoom,...(source.current?{source_version:source.current}:{})})
      if(await storage.getItem(key)!==raw)await slot.saveRaw(raw)
      if(userId!=='local' && source.current)await sync()
    }catch{if(currentScope.current===key)setError(`${label}进度备份或保存失败，旧记录保留。`)}
  }
  useEffect(()=>{void save()},[loaded,position.page,position.zoom])
  const setPage=(change:number|((previous:number)=>number))=>{
    const state=view.current,next=typeof change==='function'?change(state.page):change
    if(!Number.isSafeInteger(next)||next<1)return
    state.page=state.count?Math.min(next,state.count):next;state.renderedPage=0;state.touched=true
    setPosition({page:state.page,zoom:state.zoom})
  }
  const setZoom=(change:number|((previous:number)=>number))=>{
    const state=view.current,next=typeof change==='function'?change(state.zoom):change
    if(!Number.isFinite(next)||next<.5||next>3)return
    state.zoom=Math.round(next*1000000)/1000000;state.renderedZoom=0;state.touched=true
    setPosition({page:state.page,zoom:state.zoom})
  }
  const activate=(count:number)=>{
    if(!Number.isSafeInteger(count)||count<1)return
    view.current.count=count
    if(view.current.page>count)setPage(count)
  }
  const markRendered=(page:number,zoom:number)=>{
    if(view.current.page!==page||view.current.zoom!==zoom)return
    view.current.renderedPage=page;view.current.renderedZoom=zoom;void save()
  }
  return {...position,error,sourceVersion:source.current,setPage,setZoom,activate,markRendered,sync,
    resolveSync:async(choice:'local'|'remote')=>{const result:SyncResult=await slot.resolve(choice);if(result.status==='downloaded')restore();setError(materialSyncMessage(result));return result}}
}
