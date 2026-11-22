import { createRoot } from 'react-dom/client'
import MaterialNotebook from '../../../KnowAll_Studio/frontend/src/components/MaterialNotebook'
import { useAuthStore } from '../../../KnowAll_Studio/frontend/src/stores'
import { desktopStateSlot } from '../../../KnowAll_Studio/frontend/src/materialStateSync'
import { saveProgress } from '../../../KnowAll_Studio/frontend/src/materialProgress'
export * from '../../../KnowAll_Studio/frontend/src/api'
export function mountUser(user, token) {
  useAuthStore.setState({ user, token, isAuthenticated: true })
  createRoot(document.getElementById('app')).render(<MaterialNotebook documentId="knowledge-a" />)
}
export const syncPosition = () => desktopStateSlot('cross-a', 'knowledge-a', 'text_progress').sync()
export const resolveNote = choice => desktopStateSlot('cross-a', 'knowledge-a', 'note').resolve(choice)
export const savePosition = index => saveProgress('cross-a', 'knowledge-a', {chunkIndex:index,chunkId:'knowledge-a-'+(index+1),mode:'text',chapter:'',updatedAt:Date.now()})
