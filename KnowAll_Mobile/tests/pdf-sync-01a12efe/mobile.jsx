import {createRoot} from 'react-dom/client'
import {useEffect,useState} from 'react'
import {useOriginalProgress} from '../../src/hooks/useOriginalProgress'
import {useAuthStore} from '../../src/stores'
import {getOriginalPreview,getOriginalPage} from '../../src/api'
export * from '../../src/api'
export function mountUser(user,token,documentId='pdf-a',metadata=null){useAuthStore.setState({user,token,isAuthenticated:true,authReady:true});createRoot(document.getElementById('app')).render(<Reader documentId={documentId} metadata={metadata}/>)}
function Reader({documentId,metadata}){
  const user=useAuthStore(s=>s.user),[count,setCount]=useState(0),[image,setImage]=useState(null),[error,setError]=useState('')
  const progress=useOriginalProgress(user.id,documentId,count);window.probe=progress
  useEffect(()=>{let live=true;if(metadata)setCount(metadata.page_count);else getOriginalPreview(documentId).then(meta=>{if(live)setCount(meta.page_count)}).catch(e=>setError(String(e)));return()=>{live=false}},[])
  useEffect(()=>{let live=true;setImage(null);if(count)getOriginalPage(documentId,progress.page).then(uri=>{if(live)setImage({uri,page:progress.page})}).catch(e=>{if(live)setError(String(e))});return()=>{live=false}},[count,progress.page])
  return <><output aria-label="mobile page">{progress.page}</output><output aria-label="mobile zoom">{progress.zoom}</output>
    {image&&<img width="200" alt={'Original page '+image.page} src={image.uri} onLoad={()=>progress.markRendered(image.page)}/>}
    <button onClick={()=>progress.update({page:3,zoom:2})}>Read third</button>
    <button onClick={()=>progress.update({page:1})}>Read first</button>
    <button onClick={()=>progress.update({zoom:2})}>Zoom two</button>
    <button onClick={()=>progress.save()}>Save original</button><p role="status" aria-label="mobile sync">{progress.error}</p><p role="status" aria-label="image error">{error}</p></>
}
