import { useEffect, useMemo, useRef, useState } from 'react'
import { AppState } from 'react-native'
import { storage } from '../services/storage'
import { MaterialProgressSession, materialProgressKey, queueProgressStorage, type MaterialProgressState } from '../materialProgress'
import { createMobileStateBridge, syncMessage } from '../services/materialStateSync'
import { useAuthStore } from '../stores'

const empty: MaterialProgressState = { key: '', index: 0, fontSize: 18, ready: false, saving: false, dirty: false, error: '' }
export function useMaterialProgress(accountId: string | undefined, documentId: string | undefined, chunkCount: number, active: boolean) {
  const key = materialProgressKey(accountId, documentId)
  const touched = useRef(false)
  const pair = useMemo(() => {
    if (!key || !accountId || !documentId) return null
    touched.current = false
    let session: MaterialProgressSession
    const bridge = createMobileStateBridge(accountId, documentId, 'text_progress', () => (!touched.current || !session.snapshot().dirty) && useAuthStore.getState().user?.id === accountId)
    session = new MaterialProgressSession(bridge.store, key)
    return { session, bridge }
  }, [key])
  const session = pair?.session ?? null
  const [syncState, setSyncState] = useState({ key: '', message: '', loaded: false })
  const currentSession = useRef(session)
  currentSession.current = session
  const [observed, setObserved] = useState(empty)
  const [recentFailure, setRecentFailure] = useState({ key: '', failed: false })
  const state = observed.key === key ? observed : empty
  useEffect(() => {
    if (!session) { setObserved(empty); return }
    setObserved(session.snapshot())
    const unsubscribe = session.subscribe(setObserved)
    const stopSync = pair!.bridge.subscribe(async result => {
      // Restore the downloaded session before enabling its autosave; otherwise a fresh
      // reader's default paragraph can race with and overwrite the remote position.
      if (result.status === 'downloaded') await session.reloadSaved()
      setSyncState({ key: key!, message: syncMessage(result), loaded: true })
    })
    void session.load().then(ok => { if (ok) void pair!.bridge.sync(); else setSyncState({ key: key!, message: '', loaded: true }) })
    return () => { unsubscribe(); stopSync(); void session.flush() }
  }, [session])
  useEffect(() => { if (active) session?.activate(chunkCount) }, [session, active, chunkCount])
  useEffect(() => { if (active && state.ready && syncState.key === key && syncState.loaded) void session?.flush() }, [session, active, state.ready, state.index, state.fontSize, chunkCount, syncState.loaded, syncState.key])
  useEffect(() => {
    if (!session) return
    const subscription = AppState.addEventListener('change', status => { if (status !== 'active') void session.flush(); else void pair!.bridge.sync() })
    return () => subscription.remove()
  }, [session])
  const saveRecent = () => accountId && documentId
    ? queueProgressStorage(storage, `material-recent:${accountId}`, () => storage.setItem(`material-recent:${accountId}`, documentId))
    : Promise.resolve()
  useEffect(() => {
    if (!active || !session) return
    let live = true
    setRecentFailure({ key: session.key, failed: false })
    void saveRecent().catch(() => { if (live) setRecentFailure({ key: session.key, failed: true }) })
    return () => { live = false }
    // Update the recent material only when it opens, not on every delayed position save.
  }, [session, active])
  const retryRecent = async () => {
    try { await saveRecent(); if (currentSession.current === session) setRecentFailure({ key: key || '', failed: false }) }
    catch { if (currentSession.current === session) setRecentFailure({ key: key || '', failed: true }) }
  }
  return { ...state, error: state.error || (syncState.key === key ? syncState.message : ''), recentError: recentFailure.key === key && recentFailure.failed,
    update: (change: { index?: number; fontSize?: number }) => { touched.current = true; session?.update(change) },
    reload: () => session?.load(), save: () => session?.flush(), retryRecent,
    replaceWithCurrent: () => session?.replaceWithCurrent() ?? Promise.resolve(false),
    resolveSync: async (choice: 'local' | 'remote') => {
      const result = await pair?.bridge.slot.resolve(choice)
      if (result) { if (result.status === 'downloaded') await session?.reloadSaved(); setSyncState({ key: key || '', message: syncMessage(result), loaded: true }) }
      return result
    },
  }
}
