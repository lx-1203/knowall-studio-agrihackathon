// Root layout: Auth guard + Theme provider + Stack navigation

import { useEffect, useState } from 'react'
import { Stack } from 'expo-router'
import { useAuthStore } from '../src/stores'
import { restoreToken } from '../src/api'
import { ActivityIndicator, View } from 'react-native'
import { ThemeProvider } from '../src/components/ThemeProvider'
import ScreenPrivacyGuard from '../src/components/ScreenPrivacyGuard'

export default function RootLayout() {
  return (
    <ScreenPrivacyGuard>
      <ProtectedRootLayout />
    </ScreenPrivacyGuard>
  )
}

function ProtectedRootLayout() {
  const { isAuthenticated, authReady, restore } = useAuthStore()
  const [initializing, setInitializing] = useState(true)

  useEffect(() => {
    async function init() {
      await restoreToken()
      await restore()
      setInitializing(false)
    }
    init()
  }, [])

  if (initializing) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator size="large" color="#7C3AED" />
      </View>
    )
  }

  return (
    <ThemeProvider>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="search" options={{ headerShown: false }} />
        <Stack.Screen name="shared-library" options={{ headerShown: false }} />
        <Stack.Screen name="document-sharing" options={{ headerShown: false }} />
        <Stack.Screen name="check-in" options={{ headerShown: false }} />
        <Stack.Screen name="login" options={{ presentation: 'modal', headerShown: false }} />
        <Stack.Screen
          name="kaf"
          options={{ headerShown: false, animation: 'slide_from_right' }}
        />
        <Stack.Screen name="glossary" options={{ headerShown: true, headerTitle: '名词解释库', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="summaries" options={{ headerShown: true, headerTitle: '学习摘要', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="interactive-quiz" options={{ headerShown: true, headerTitle: '互动问答', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="coverage" options={{ headerShown: true, headerTitle: '覆盖率报告', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="language" options={{ headerShown: true, headerTitle: '阅读练习', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="share" options={{ headerShown: true, headerTitle: '分享中心', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="books" options={{ headerShown: true, headerTitle: '教材编译', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="research" options={{ headerShown: true, headerTitle: '深度研究', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
        <Stack.Screen name="sync-editor" options={{ headerShown: true, headerTitle: '实时协同', headerStyle: { backgroundColor: '#7C3AED' }, headerTintColor: '#fff' }} />
      </Stack>
    </ThemeProvider>
  )
}
