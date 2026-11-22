type ProtectionStatus = 'preparing' | 'protected' | 'error'

export type ScreenPrivacySnapshot = Readonly<{
  status: ProtectionStatus
  appState: string
  concealed: boolean
}>

type ProtectionDependencies = {
  isAvailable: () => Promise<boolean>
  preventCapture: () => Promise<void>
  protectAppSwitcher?: () => Promise<void>
}

/** App-lifetime protection: a failure must never reveal business content. */
export function createScreenPrivacyController(dependencies: ProtectionDependencies) {
  let snapshot: ScreenPrivacySnapshot = { status: 'preparing', appState: 'unknown', concealed: true }
  let inFlight: Promise<void> | null = null
  let captureEnabled = false
  const listeners = new Set<() => void>()

  function publish(status: ProtectionStatus, appState = snapshot.appState) {
    if (status === snapshot.status && appState === snapshot.appState) return
    snapshot = { status, appState, concealed: status !== 'protected' || appState !== 'active' }
    listeners.forEach(listener => listener())
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    setAppState(appState: string) {
      publish(snapshot.status, appState)
    },
    ensureProtection(): Promise<void> {
      if (inFlight) return inFlight
      if (snapshot.status === 'protected') return Promise.resolve()
      publish('preparing')
      inFlight = Promise.resolve().then(async () => {
        try {
          if (!captureEnabled) {
            if (!await dependencies.isAvailable()) throw new Error('Capture protection unavailable')
            await dependencies.preventCapture()
            captureEnabled = true
          }
          await dependencies.protectAppSwitcher?.()
          publish('protected')
        } catch {
          // Do not expose native error details or silently continue without protection.
          publish('error')
        } finally {
          inFlight = null
        }
      })
      return inFlight
    },
  }
}
