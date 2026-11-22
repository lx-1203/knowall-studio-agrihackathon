import {createRoot} from 'react-dom/client'
import ImagePreview from '../../src/components/previews/ImagePreview'
import {useAuthStore} from '../../src/stores'
import api from '../../src/api'
export * from '../../src/api'
export async function mountUser(user,token,documentId='image-a'){
  useAuthStore.setState({user,token,isAuthenticated:true})
  const response=await api.get(`/documents/${documentId}/raw`,{responseType:'blob'})
  const fileType=documentId.slice(6)==='apng'?'png':documentId.slice(6)
  createRoot(document.getElementById('app')).render(<div style={{width:320}}><ImagePreview documentId={documentId} fileUrl={URL.createObjectURL(response.data)} fileType={fileType} fileName="Original.png"/></div>)
}
