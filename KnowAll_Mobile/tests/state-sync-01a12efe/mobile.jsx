import { createRoot } from 'react-dom/client'
import { useMaterialNote } from '../../src/hooks/useMaterialNote'
import { useMaterialProgress } from '../../src/hooks/useMaterialProgress'
import { useAuthStore } from '../../src/stores'
export * from '../../src/api'
export function mountUser(user, token) {
  useAuthStore.setState({ user, token, isAuthenticated: true, authReady: true })
  createRoot(document.getElementById('app')).render(<Probe />)
}
function Probe() {
  const id = useAuthStore(s => s.user.id)
  const note = useMaterialNote(id, 'knowledge-a')
  const progress = useMaterialProgress(id, 'knowledge-a', 2, true)
  window.probe = { note, progress }
  return <><input aria-label="mobile note" value={note.text} disabled={!note.ready} onChange={e => note.update(e.target.value)} />
    <button onClick={() => { void note.save() }}>Save note</button><p role="status" aria-label="mobile sync">{note.error}</p>
    <output aria-label="mobile index">{progress.index}</output>
    <button onClick={() => progress.update({ index: 1, fontSize: 24 })}>Read second</button>
    <button onClick={() => { void progress.save() }}>Save progress</button><p>{progress.error}</p></>
}
