import {createRoot} from 'react-dom/client'
import PdfPreview from '../../src/components/previews/PdfPreview'
import {useAuthStore} from '../../src/stores'
import api from '../../src/api'
export * from '../../src/api'
export async function mountUser(user,token){
  useAuthStore.setState({user,token,isAuthenticated:true})
  const response=await api.get('/documents/pdf-a/raw',{responseType:'blob'})
  createRoot(document.getElementById('app')).render(<PdfPreview documentId="pdf-a" fileUrl={URL.createObjectURL(response.data)} fileName="Original.pdf"/>)
}
