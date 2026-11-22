import { useEffect, useMemo, useState } from 'react'
import { AppState, Platform } from 'react-native'
import { storage } from '../services/storage'
import { MaterialNoteSession, materialNoteKey, type MaterialNoteState } from '../materialNotes'
import { createMobileStateBridge, syncMessage } from '../services/materialStateSync'
import { useAuthStore } from '../stores'

const empty: MaterialNoteState = { key: '', text: '', ready: false, saving: false, dirty: false, persisted: false, error: '' }
export function useMaterialNote(accountId?: string, documentId?: string) {
  const key = materialNoteKey(accountId, documentId)
  const pair = useMemo(() => {
    if (!key || !accountId || !documentId) return null
    let session: MaterialNoteSession
    const bridge = createMobileStateBridge(accountId, documentId, 'note', () => session.snapshot().ready && !session.snapshot().dirty && useAuthStore.getState().user?.id === accountId)
    session = new MaterialNoteSession(bridge.store, key)
    return { session, bridge }
  }, [key])
  const session = pair?.session ?? null
  const [syncState, setSyncState] = useState({ key: '', message: '' })
  const [observed, setObserved] = useState<MaterialNoteState>(empty)
  // Never render the previous account/document's text during the effect transition.
  const state = observed.key === key ? observed : empty
  useEffect(() => {
    if (!session) { setObserved(empty); return }
    setObserved(session.snapshot())
    const unsubscribe = session.subscribe(setObserved)
    const stopSync = pair!.bridge.subscribe(result => {
      setSyncState({ key: key!, message: syncMessage(result) })
      if (result.status === 'downloaded' && !session.snapshot().dirty) void session.load()
    })
    void session.load().then(ok => { if (ok) void pair!.bridge.sync() })
    return () => { unsubscribe(); stopSync(); void session.flush() }
  }, [session])
  useEffect(() => {
    if (!session || !state.dirty) return
    const timer = setTimeout(() => { void session.flush() }, 500)
    return () => clearTimeout(timer)
  }, [session, state.text, state.dirty])
  useEffect(() => {
    if (!session) return
    const subscription = AppState.addEventListener('change', status => { if (status !== 'active') void session.flush(); else void pair!.bridge.sync() })
    return () => subscription.remove()
  }, [session])
  useEffect(() => {
    if (Platform.OS !== 'web' || !(state.dirty || state.saving) || typeof window === 'undefined') return
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; void session?.flush() }
    window.addEventListener('beforeunload', protect)
    return () => window.removeEventListener('beforeunload', protect)
  }, [session, state.dirty, state.saving])
  return { ...state, error: state.error || (syncState.key === key ? syncState.message : ''), update: (text: string) => session?.update(text),
    save: async () => { const ok = await (session?.flush() ?? Promise.resolve(false)); if (ok) void pair?.bridge.sync(); return ok },
    reload: async () => { const ok = await (session?.load() ?? Promise.resolve(false)); if (ok) void pair?.bridge.sync(); return ok },
    resolveSync: async (choice: 'local' | 'remote') => {
      const result = await pair?.bridge.slot.resolve(choice)
      if (result) { setSyncState({ key: key || '', message: syncMessage(result) }); if (result.status === 'downloaded') await session?.load() }
      return result
    },
  }
}
