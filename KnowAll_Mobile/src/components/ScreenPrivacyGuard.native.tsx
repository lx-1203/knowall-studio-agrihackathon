import React, { useEffect, useSyncExternalStore, type ReactNode } from 'react'
import { ActivityIndicator, AppState, Platform, Pressable, StyleSheet, Text, View } from 'react-native'
import { createScreenPrivacyController } from '../screenPrivacy'

let captureAttempt = 0
// Keep this controller for the whole app lifetime, including StrictMode remounts.
// Never call allowScreenCaptureAsync during navigation, backgrounding or cleanup.
const privacy = createScreenPrivacyController({
  isAvailable: async () => {
    // Loading inside initialization lets missing native binaries fail closed
    // into the recovery page instead of throwing while the root module loads.
    const screenCapture = await import('expo-screen-capture')
    return screenCapture.isAvailableAsync()
  },
  preventCapture: async () => {
    // Expo retains a key even if its native call rejects. A retry needs a new key;
    // successful activation is deduplicated by the controller above.
    const screenCapture = await import('expo-screen-capture')
    await screenCapture.preventScreenCaptureAsync(`knowall-privacy-${++captureAttempt}`)
  },
  protectAppSwitcher: Platform.OS === 'ios'
    ? async () => {
      const screenCapture = await import('expo-screen-capture')
      await screenCapture.enableAppSwitcherProtectionAsync(1)
    }
    : undefined,
})

const descriptions = {
  preparing: '正在启用屏幕保护…',
  protected: '返回应用后继续查看。',
  error: '为保护你的资料，页面已暂停显示。请重试；若仍无法启用，请更新应用并重新打开。',
}

export default function ScreenPrivacyGuard({ children }: { children: ReactNode }) {
  const snapshot = useSyncExternalStore(privacy.subscribe, privacy.getSnapshot, privacy.getSnapshot)

  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => privacy.setAppState(state))
    privacy.setAppState(AppState.currentState ?? 'unknown')
    void privacy.ensureProtection()
    return () => subscription.remove()
  }, [])

  return (
    <View style={styles.root}>
      <View
        style={[styles.content, snapshot.concealed && styles.concealed]}
        pointerEvents={snapshot.concealed ? 'none' : 'auto'}
        accessibilityElementsHidden={snapshot.concealed}
        importantForAccessibility={snapshot.concealed ? 'no-hide-descendants' : 'auto'}
      >
        {snapshot.status === 'protected' ? children : null}
      </View>
      {snapshot.concealed && (
        <View style={styles.shield} accessibilityViewIsModal>
          {snapshot.status === 'preparing' && <ActivityIndicator size="large" color="#32695B" />}
          <Text style={styles.title} accessibilityRole="header">
            {snapshot.status === 'error' ? '暂时无法启用屏幕保护' : '内容已隐藏'}
          </Text>
          <Text style={styles.description}>
            {descriptions[snapshot.status]}
          </Text>
          {snapshot.status === 'error' && (
            <Pressable
              accessibilityRole="button"
              onPress={() => { void privacy.ensureProtection() }}
              style={styles.retry}
            >
              <Text style={styles.retryText}>重新启用保护</Text>
            </Pressable>
          )}
        </View>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#F8F8F4' },
  content: { flex: 1 },
  concealed: { opacity: 0 },
  shield: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    backgroundColor: '#F8F8F4',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    gap: 16,
  },
  title: { color: '#242A26', fontSize: 20, fontWeight: '600', textAlign: 'center' },
  description: { color: '#6C726C', fontSize: 15, lineHeight: 24, textAlign: 'center', maxWidth: 360 },
  retry: { backgroundColor: '#32695B', paddingVertical: 14, paddingHorizontal: 24, borderRadius: 12 },
  retryText: { color: '#FFFFFF', fontSize: 16, fontWeight: '600' },
})
